"""可终止的同步 capability 执行边界。

生产 Agent Loop 默认把同步 capability 放入独立解释器进程。协作式进程内
执行器只用于无需硬终止语义的单元测试与受控嵌入场景，生产组合根不得回退。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

from omnicell_agent.capabilities.contracts import ArtifactRef
from omnicell_agent.capabilities.errors import (
    CapabilityExecutionError,
    CapabilityInputError,
)
from omnicell_agent.capabilities.registry import CapabilityContext, CapabilityRegistry
from omnicell_agent.runtime.cancellation import runtime_cancellation_scope
from omnicell_agent.runtime.docker_cli import DockerCLI

from .cancellation import CancellationToken, RunCancelledError


logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1
_DEFAULT_BOOTSTRAP = (
    "omnicell_agent.capabilities.bootstrap:build_domain_capability_layer"
)
_REQUEST_MAX_BYTES = 512 * 1024
_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
_ACTIVITY_FRAME_MAX_BYTES = 56 * 1024
_ACTIVITY_TOTAL_MAX_BYTES = 8 * 1024 * 1024
_CONTAINER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IMMUTABLE_CONTAINER_ID = re.compile(r"^(?:sha256:)?[0-9a-f]{12,64}$")
_INVOCATION_ID = re.compile(r"^[0-9a-f]{32}$")
_RUNTIME_CONTROL_ROOT = ".omnicell-runtime-control"
_PROVISIONAL_CLEANUP_TIMEOUT_SECONDS = 65.0
_PROVISIONAL_CLEANUP_POLL_SECONDS = 0.25
_ARTIFACT_FIELDS = frozenset(ArtifactRef.model_fields)


class CapabilityProcessError(RuntimeError):
    """隔离进程未能返回可信 capability 结果。"""


class RuntimeCleanupError(CapabilityProcessError):
    """精确 runtime 回收尚未得到确认，禁止终态或新执行。"""


RuntimeActivityCallback = Callable[[Mapping[str, Any]], Awaitable[None]]


class CapabilityInvoker(Protocol):
    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        cancellation: CancellationToken,
        on_activity: RuntimeActivityCallback | None = None,
    ) -> BaseModel: ...


CapabilityInvokerFactory = Callable[
    [CapabilityRegistry, CapabilityContext], CapabilityInvoker
]


def _runtime_claim_root(workspace: Path, *, create: bool) -> Path:
    root = workspace.expanduser().resolve(strict=True)
    identity = hashlib.sha256(os.fspath(root).encode("utf-8")).hexdigest()
    control_parent = root.parent / _RUNTIME_CONTROL_ROOT / "claims"
    claim_root = control_parent / identity
    try:
        if create:
            claim_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not claim_root.exists():
            return claim_root
        if claim_root.is_symlink() or not claim_root.is_dir():
            raise CapabilityProcessError("runtime claim root 非法")
        resolved_parent = root.parent.resolve(strict=True)
        resolved_root = claim_root.resolve(strict=True)
        resolved_root.relative_to(resolved_parent)
        os.chmod(resolved_root, 0o700)
    except CapabilityProcessError:
        raise
    except (OSError, ValueError) as exc:
        raise CapabilityProcessError("runtime claim root 无法安全创建") from exc
    return resolved_root


def _runtime_claim_path(workspace: Path, invocation_id: str) -> Path:
    if _INVOCATION_ID.fullmatch(invocation_id) is None:
        raise CapabilityProcessError("runtime invocation identity 非法")
    return _runtime_claim_root(workspace, create=True) / f"{invocation_id}.json"


def _discard_invocation_scope(workspace: Path, invocation_id: str) -> None:
    private_root = workspace / ".omnicell-invocations"
    target = private_root / invocation_id
    if not target.exists() and not target.is_symlink():
        return
    try:
        if private_root.is_symlink() or not private_root.is_dir():
            raise CapabilityProcessError("invocation artifact root 非法")
        resolved_root = private_root.resolve(strict=True)
        resolved_root.relative_to(workspace)
        if target.is_symlink():
            target.unlink()
            return
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(resolved_root)
        shutil.rmtree(target)
    except CapabilityProcessError:
        raise
    except (OSError, ValueError) as exc:
        raise CapabilityProcessError("invocation artifact scope 无法安全清理") from exc


async def _cleanup_owned_container_claim(
    invocation_id: str,
    ownership_path: Path,
) -> bool:
    if not ownership_path.is_file():
        return True
    if ownership_path.is_symlink():
        raise CapabilityProcessError("runtime ownership claim 不能是 symlink")
    if ownership_path.name != f"{invocation_id}.json":
        raise CapabilityProcessError("runtime ownership claim 文件名不匹配")
    try:
        claim = json.loads(ownership_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapabilityProcessError("runtime ownership claim 无法解析") from exc
    if not isinstance(claim, dict) or claim.get("invocation_id") != invocation_id:
        raise CapabilityProcessError("runtime ownership claim identity 不匹配")
    claimed_identifier = str(claim.get("container_id") or "")
    if not _CONTAINER_ID.fullmatch(claimed_identifier):
        raise CapabilityProcessError("runtime ownership container identity 非法")
    claim_state = str(claim.get("state") or "")
    if claim_state not in {"provisional", "confirmed"}:
        raise CapabilityProcessError("runtime ownership claim state 非法")

    docker = DockerCLI()
    try:
        inspected = await docker.run(
            ("container", "inspect", claimed_identifier),
            timeout=10,
            stdout_max_bytes=64 * 1024,
            stderr_max_bytes=8 * 1024,
            check=False,
        )
    except Exception as exc:
        raise CapabilityProcessError(
            "无法验证 capability-owned Docker 容器"
        ) from exc
    if inspected.returncode != 0:
        missing = inspected.stderr.lower()
        if b"no such container" in missing or b"no such object" in missing:
            if claim_state == "confirmed":
                ownership_path.unlink(missing_ok=True)
                return True
            # docker run may still be creating the provisionally named
            # container in its own process. Preserve the durable claim so a
            # later lease holder can retry exact, label-verified cleanup.
            return False
        raise CapabilityProcessError("无法验证 capability-owned Docker 容器")
    try:
        payload = json.loads(inspected.stdout)
        record = payload[0]
        labels = record["Config"]["Labels"] or {}
        immutable_id = str(record["Id"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CapabilityProcessError("Docker inspect ownership 响应非法") from exc
    if labels.get("omnicell.runtime.invocation") != invocation_id:
        raise CapabilityProcessError("Docker container ownership label 不匹配")
    if _IMMUTABLE_CONTAINER_ID.fullmatch(immutable_id) is None:
        raise CapabilityProcessError("Docker immutable container identity 非法")

    try:
        removed = await docker.run(
            ("rm", "--force", immutable_id),
            timeout=10,
            stdout_max_bytes=64 * 1024,
            stderr_max_bytes=8 * 1024,
            check=False,
        )
    except Exception as exc:
        raise CapabilityProcessError(
            "无法清理 capability-owned Docker 容器"
        ) from exc
    missing = removed.stderr.lower()
    if removed.returncode != 0 and not (
        b"no such container" in missing or b"no such object" in missing
    ):
        raise CapabilityProcessError("无法清理 capability-owned Docker 容器")
    ownership_path.unlink(missing_ok=True)
    return True


async def _await_owned_container_cleanup(
    invocation_id: str,
    ownership_path: Path,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PROVISIONAL_CLEANUP_TIMEOUT_SECONDS
    while True:
        try:
            cleaned = await _cleanup_owned_container_claim(
                invocation_id,
                ownership_path,
            )
        except RuntimeCleanupError:
            raise
        except CapabilityProcessError as exc:
            raise RuntimeCleanupError(
                "Docker runtime 回收校验失败"
            ) from exc
        if cleaned:
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeCleanupError(
                "provisional Docker runtime 回收尚未确认"
            )
        await asyncio.sleep(min(_PROVISIONAL_CLEANUP_POLL_SECONDS, remaining))


async def reap_workspace_runtime_claims(
    workspace: str | Path,
    *,
    max_claims: int = 1_000,
) -> tuple[str, ...]:
    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        claim_root = _runtime_claim_root(root, create=False)
        if not claim_root.exists():
            return ()
        if (
            claim_root.is_symlink()
            or not claim_root.is_dir()
            or claim_root.resolve(strict=True) != claim_root
        ):
            raise CapabilityProcessError("runtime claim root 非法")
        claims = sorted(claim_root.glob("*.json"))
        if len(claims) > max_claims:
            raise CapabilityProcessError("runtime ownership claims 超过回收上限")
        reaped: list[str] = []
        for claim_path in claims:
            invocation_id = claim_path.stem
            if (
                _INVOCATION_ID.fullmatch(invocation_id) is None
                or claim_path.is_symlink()
                or not claim_path.is_file()
            ):
                raise CapabilityProcessError("runtime ownership claim 文件非法")
            await _await_owned_container_cleanup(invocation_id, claim_path)
            await asyncio.to_thread(
                _discard_invocation_scope, root, invocation_id
            )
            reaped.append(invocation_id)
        return tuple(reaped)
    except RuntimeCleanupError:
        raise
    except CapabilityProcessError as exc:
        raise RuntimeCleanupError("runtime ownership claim 回收失败") from exc
    except Exception as exc:
        raise RuntimeCleanupError("runtime ownership claim 回收失败") from exc


def _artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: dict[str, ArtifactRef] = {}

    def visit(candidate: Any) -> None:
        if isinstance(candidate, BaseModel):
            visit(candidate.model_dump(mode="json"))
            return
        if isinstance(candidate, Mapping):
            if _ARTIFACT_FIELDS.issubset(candidate):
                try:
                    ref = ArtifactRef.model_validate(candidate)
                except Exception:
                    pass
                else:
                    found[str(ref.artifact_id)] = ref
                    return
            for nested in candidate.values():
                visit(nested)
            return
        if isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)

    visit(value)
    return tuple(found[key] for key in sorted(found))


class CooperativeInProcessCapabilityInvoker:
    """仅提供协作式取消；显式命名以避免被误当作生产隔离边界。"""

    def __init__(
        self,
        registry: CapabilityRegistry,
        context: CapabilityContext,
    ) -> None:
        self._registry = registry
        self._context = context

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        cancellation: CancellationToken,
        on_activity: RuntimeActivityCallback | None = None,
    ) -> BaseModel:
        del on_activity
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._invoke_sync,
                name,
                dict(arguments),
                cancellation,
            )
        )
        cancel_waiter = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {worker, cancel_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_waiter in done:
                await cancellation.propagate()
            else:
                cancel_waiter.cancel()
            return await worker
        except asyncio.CancelledError:
            cancellation.cancel("capability execution cancelled")
            await cancellation.propagate()
            try:
                await worker
            except BaseException:
                pass
            raise
        finally:
            if not cancel_waiter.done():
                cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)

    def _invoke_sync(
        self,
        name: str,
        arguments: dict[str, Any],
        cancellation: CancellationToken,
    ) -> BaseModel:
        with runtime_cancellation_scope(cancellation.runtime):
            return self._registry.invoke(name, arguments, self._context)


class SubprocessCapabilityInvoker:
    """每次调用使用独立进程，并在取消时等待整个进程组退出。"""

    def __init__(
        self,
        registry: CapabilityRegistry,
        context: CapabilityContext,
        *,
        bootstrap_target: str = _DEFAULT_BOOTSTRAP,
        child_env: Mapping[str, str] | None = None,
        termination_grace_seconds: float = 3.0,
    ) -> None:
        if ":" not in bootstrap_target:
            raise ValueError("capability bootstrap_target 必须使用 module:callable 格式")
        if termination_grace_seconds <= 0 or termination_grace_seconds > 10:
            raise ValueError("termination_grace_seconds 必须在 0..10 秒之间")
        self._registry = registry
        self._context = context
        self._bootstrap_target = bootstrap_target
        self._child_env = dict(child_env or {})
        self._termination_grace_seconds = termination_grace_seconds

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        cancellation: CancellationToken,
        on_activity: RuntimeActivityCallback | None = None,
    ) -> BaseModel:
        cancellation.raise_if_cancelled()
        handler = self._registry.get(name)
        request = handler.request_model.model_validate(arguments)
        normalized_arguments = request.model_dump(mode="json")
        trusted_artifacts = _artifact_refs(normalized_arguments)
        for ref in trusted_artifacts:
            self._context.artifacts.resolve(ref)

        invocation_id = uuid4().hex
        runtime_ownership_path = _runtime_claim_path(
            self._context.artifacts.workspace,
            invocation_id,
        )
        payload = json.dumps(
            {
                "protocol_version": _PROTOCOL_VERSION,
                "invocation_id": invocation_id,
                "conversation_id": str(self._context.conversation_id),
                "workspace": str(self._context.artifacts.workspace),
                "capability": name,
                "arguments": normalized_arguments,
                "trusted_artifacts": [
                    ref.model_dump(mode="json") for ref in trusted_artifacts
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _REQUEST_MAX_BYTES:
            raise ValueError(
                f"capability process request 超过 {_REQUEST_MAX_BYTES} bytes"
            )

        with tempfile.TemporaryDirectory(prefix="omnicell-capability-") as temporary:
            response_path = Path(temporary) / "response.json"
            watchdog_marker_path = Path(temporary) / "watchdog-expired"
            environment = os.environ.copy()
            environment.update(self._child_env)
            trusted_package_root = str(Path(__file__).resolve().parents[2])
            inherited_pythonpath = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = os.pathsep.join(
                value
                for value in (trusted_package_root, inherited_pythonpath)
                if value
            )
            environment["OMNICELL_CAPABILITY_INVOCATION_ID"] = invocation_id
            environment["OMNICELL_RUNTIME_OWNERSHIP_FILE"] = str(
                runtime_ownership_path
            )
            control_read, control_write = os.pipe()
            activity_read, activity_write = os.pipe()
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "omnicell_agent.capabilities.worker",
                    "--bootstrap",
                    self._bootstrap_target,
                    "--response",
                    str(response_path),
                    "--control-fd",
                    str(control_read),
                    "--activity-fd",
                    str(activity_write),
                    "--watchdog-timeout",
                    str(cancellation.lease_watchdog_timeout_seconds or 0),
                    "--watchdog-marker",
                    str(watchdog_marker_path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=temporary,
                    env=environment,
                    start_new_session=True,
                    pass_fds=(control_read, activity_write),
                )
            except BaseException:
                os.close(control_write)
                os.close(activity_read)
                os.close(activity_write)
                raise
            finally:
                os.close(control_read)
                os.close(activity_write)
            communication = asyncio.create_task(process.communicate(payload))
            cancel_waiter = asyncio.create_task(cancellation.wait())
            activity_forwarder = asyncio.create_task(
                self._forward_runtime_activities(
                    activity_read,
                    on_activity=on_activity,
                )
            )
            lease_forwarder = asyncio.create_task(
                self._forward_lease_renewals(cancellation, control_write)
            )
            try:
                activity_finished = False
                while True:
                    waiters = {communication, cancel_waiter}
                    if not activity_finished:
                        waiters.add(activity_forwarder)
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_waiter in done:
                        await self._terminate(
                            process, invocation_id, runtime_ownership_path
                        )
                        await asyncio.gather(communication, return_exceptions=True)
                        await asyncio.to_thread(
                            self._discard_invocation,
                            invocation_id,
                        )
                        cancellation.raise_if_cancelled()
                    if activity_forwarder in done:
                        activity_forwarder.result()
                        activity_finished = True
                    if communication in done:
                        await communication
                        await activity_forwarder
                        break
            except RunCancelledError:
                raise
            except RuntimeCleanupError:
                await asyncio.gather(communication, return_exceptions=True)
                raise
            except asyncio.CancelledError:
                cancellation.cancel("capability process task cancelled")
                await self._terminate(
                    process, invocation_id, runtime_ownership_path
                )
                await asyncio.gather(communication, return_exceptions=True)
                await asyncio.to_thread(self._discard_invocation, invocation_id)
                raise
            except BaseException:
                await self._terminate(
                    process, invocation_id, runtime_ownership_path
                )
                await asyncio.gather(communication, return_exceptions=True)
                await asyncio.to_thread(self._discard_invocation, invocation_id)
                raise
            finally:
                if not cancel_waiter.done():
                    cancel_waiter.cancel()
                await asyncio.gather(cancel_waiter, return_exceptions=True)
                if not activity_forwarder.done():
                    activity_forwarder.cancel()
                await asyncio.gather(activity_forwarder, return_exceptions=True)
                lease_forwarder.cancel()
                await asyncio.gather(lease_forwarder, return_exceptions=True)
                os.close(control_write)

            if process.returncode == 75 or watchdog_marker_path.is_file():
                await _await_owned_container_cleanup(
                    invocation_id,
                    runtime_ownership_path,
                )
                await asyncio.to_thread(self._discard_invocation, invocation_id)
                cancellation.cancel("capability lease watchdog expired")
                cancellation.raise_if_cancelled()

            try:
                response = self._read_response(
                    response_path,
                    returncode=process.returncode,
                )
            except BaseException:
                await _await_owned_container_cleanup(
                    invocation_id,
                    runtime_ownership_path,
                )
                await asyncio.to_thread(self._discard_invocation, invocation_id)
                raise

        # A protocol response, including a controlled failure, does not prove
        # nested Docker runtime cleanup. Resolve the durable claim before the
        # result can become a Tool fact or an Agent-visible error.
        await _await_owned_container_cleanup(
            invocation_id,
            runtime_ownership_path,
        )

        if response.get("ok") is not True:
            await asyncio.to_thread(self._discard_invocation, invocation_id)
            message = str(response.get("message") or "capability process 执行失败")[:2_000]
            error_type = str(response.get("error_type") or "")
            if error_type == "CapabilityInputError":
                raise CapabilityInputError(message)
            if error_type == "CapabilityExecutionError":
                raise CapabilityExecutionError(message)
            raise CapabilityProcessError(
                f"{error_type or 'ChildError'}: isolated capability failed"
            )

        try:
            result = handler.result_model.model_validate(response.get("result"))
            for ref in _artifact_refs(result):
                try:
                    self._context.artifacts.resolve(ref)
                except Exception:
                    self._context.artifacts.register_trusted(ref)
        except BaseException:
            await asyncio.to_thread(self._discard_invocation, invocation_id)
            raise
        return result

    def _discard_invocation(self, invocation_id: str) -> None:
        _discard_invocation_scope(
            self._context.artifacts.workspace,
            invocation_id,
        )

    @staticmethod
    async def _forward_lease_renewals(
        cancellation: CancellationToken,
        descriptor: int,
    ) -> None:
        generation = cancellation.lease_generation
        await asyncio.to_thread(os.write, descriptor, b"renew\n")
        while True:
            generation = await cancellation.wait_for_lease_renewal(generation)
            await asyncio.to_thread(os.write, descriptor, b"renew\n")

    @staticmethod
    async def _forward_runtime_activities(
        descriptor: int,
        *,
        on_activity: RuntimeActivityCallback | None,
    ) -> None:
        buffered = b""
        observed = 0
        allowed_kinds = {
            "runtime.command_started",
            "runtime.output",
            "runtime.command_completed",
        }
        try:
            while True:
                chunk = await asyncio.to_thread(os.read, descriptor, 64 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > _ACTIVITY_TOTAL_MAX_BYTES:
                    raise CapabilityProcessError(
                        "runtime activity 总量超过上限"
                    )
                buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    if not line:
                        continue
                    if len(line) > _ACTIVITY_FRAME_MAX_BYTES:
                        raise CapabilityProcessError(
                            "runtime activity frame 超过上限"
                        )
                    try:
                        activity = json.loads(line)
                    except (UnicodeError, json.JSONDecodeError) as exc:
                        raise CapabilityProcessError(
                            "runtime activity frame 不是合法 JSON"
                        ) from exc
                    if (
                        not isinstance(activity, dict)
                        or activity.get("kind") not in allowed_kinds
                    ):
                        raise CapabilityProcessError(
                            "runtime activity frame 类型非法"
                        )
                    if on_activity is not None:
                        await on_activity(activity)
            if buffered:
                raise CapabilityProcessError(
                    "runtime activity 最后一帧不完整"
                )
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_response(
        response_path: Path,
        *,
        returncode: int | None,
    ) -> dict[str, Any]:
        if not response_path.is_file():
            raise CapabilityProcessError(
                "capability process 未返回协议响应"
                f"（returncode={returncode}）"
            )
        size = response_path.stat().st_size
        if size <= 0 or size > _RESPONSE_MAX_BYTES:
            raise CapabilityProcessError("capability process 响应为空或超过上限")
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CapabilityProcessError("capability process 响应不是合法 JSON") from exc
        if not isinstance(response, dict):
            raise CapabilityProcessError("capability process 响应必须是 object")
        if response.get("protocol_version") != _PROTOCOL_VERSION:
            raise CapabilityProcessError("capability process 协议版本不匹配")
        ok = response.get("ok")
        if ok is True and returncode != 0:
            raise CapabilityProcessError("capability 成功响应与进程退出状态冲突")
        if ok is False and returncode == 0:
            raise CapabilityProcessError("capability 失败响应与进程退出状态冲突")
        if not isinstance(ok, bool):
            raise CapabilityProcessError("capability process 响应缺少 boolean ok")
        return response

    async def _terminate(
        self,
        process: asyncio.subprocess.Process,
        invocation_id: str,
        runtime_ownership_path: Path,
    ) -> None:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (PermissionError, ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self._termination_grace_seconds
                )
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (PermissionError, ProcessLookupError):
                    if process.returncode is None:
                        process.kill()
                await process.wait()
        await _await_owned_container_cleanup(invocation_id, runtime_ownership_path)


__all__ = [
    "CapabilityInvoker",
    "CapabilityInvokerFactory",
    "CapabilityProcessError",
    "CooperativeInProcessCapabilityInvoker",
    "SubprocessCapabilityInvoker",
    "RuntimeCleanupError",
    "RuntimeActivityCallback",
    "reap_workspace_runtime_claims",
]
