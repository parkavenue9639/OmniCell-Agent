import os
import uuid
import time
import logging
import docker
from jupyter_client import BlockingKernelClient
from omnicell_agent.core.config import project_root

logger = logging.getLogger(__name__)

class DockerJupyterSandbox:
    """
    提供一个驻留于 Docker 容器内的 Jupyter Kernel 执行环境。
    解决痛点：将数 GB 的 adata 实例固化于容器内存，避免频繁 I/O 与冷启动开销。
    """

    def __init__(self, image_name: str = "omnicell-worker:latest", timeout_secs: int = 120):
        self.image_name = image_name
        self.timeout_secs = timeout_secs
        
        # 尝试连接本地宿主机的 docker daemon
        try:
            self.docker_client = docker.from_env()
        except Exception:
            # 兼容 Mac OrbStack 等本地替代方案
            sock_path = os.path.expanduser("~/.orbstack/run/docker.sock")
            if os.path.exists(sock_path):
                try:
                    self.docker_client = docker.DockerClient(base_url=f"unix://{sock_path}")
                except Exception as e2:
                    logger.error(f"Failed to connect via orbstack sock: {e2}")
                    raise e2
            else:
                logger.error("Failed to connect to Docker daemon via env and no orbstack sock found.")
                raise RuntimeError("Docker daemon connection failed")

        # 指定一个独特的 session ID，用于标记当前流水线的容器实例
        self.session_id = str(uuid.uuid4())[:8]
        self.container_name = f"omnicell_sandbox_{self.session_id}"
        
        self.container = None
        self.kernel_client = None
        
        # 挂载宿主机的某个共享目录到容器中，方便读取本项目的 /data 或导出报告结果
        self._setup_volume_mounts()

    def _setup_volume_mounts(self):
        # 挂载根目录下的 data 文件夹，容器内部映射到 /app/data
        self.host_data_dir = str(project_root / "data")
        # 确保宿主机侧 data 目录存在
        os.makedirs(self.host_data_dir, exist_ok=True)
        self.container_data_dir = "/app/data"
        
        # 必须向容器传递一个临时存放 Jupyter Connection File 的目录
        # 这样宿主机的 jupyter_client 才能查阅这个 JSON 拿到 TCP 端口去连接 Kernel
        self.host_runtime_dir = str(project_root / ".runtime_kernels")
        os.makedirs(self.host_runtime_dir, exist_ok=True)
        self.container_runtime_dir = "/app/runtime"

    def start(self):
        """
        拉起 Docker 容器，并确保它在内部启动了一个暴露在 0.0.0.0 的 ipykernel。
        """
        logger.info(f"Starting Jupyter Sandbox Container: {self.container_name} with image {self.image_name}")
        
        volumes = {
            self.host_data_dir: {'bind': self.container_data_dir, 'mode': 'rw'},
            self.host_runtime_dir: {'bind': self.container_runtime_dir, 'mode': 'rw'}
        }
        
        # 指定容器必须将 connection_file 输出到挂载目录
        connection_file_name = f"kernel-{self.session_id}.json"
        
        # 内部启动指令：运行 ipykernel 并且让其把连接配置写到挂载磁盘
        cmd = [
            "python", "-m", "ipykernel_launcher", 
            "-f", f"{self.container_runtime_dir}/{connection_file_name}"
        ]

        try:
            self.container = self.docker_client.containers.run(
                image=self.image_name,
                name=self.container_name,
                command=cmd,
                volumes=volumes,
                detach=True,
                network_mode="host", 
                # 这里暂时采用 net=host 使得 jupyter_client 可以直接读取 localhost 相应的 ZeroMQ 端口
                # 对于 Mac 上的 Docker Desktop，network_mode="host" 可能表现不一致。
                # 后面可以改用 bridge 并暴露 ports={'50000-50100/tcp': ...}
            )
            
            # 等待 Jupyter Kernel 启动并把 Connection File 刷入磁盘
            self._wait_for_connection_file(connection_file_name)
            
            # 建立宿主机的连接端
            connection_file_path = os.path.join(self.host_runtime_dir, connection_file_name)
            
            # Mac Host 上读取容器生成的 connection file，如果容器内写的是 127.0.0.1 但我们没用 net host，
            # 需要替换 JSON 里的 ip 为 localhost 或容器内 IP。
            logger.info(f"Loading connection file: {connection_file_path}")
            
            self.kernel_client = BlockingKernelClient(connection_file=connection_file_path)
            self.kernel_client.load_connection_file()
            self.kernel_client.start_channels()
            self.kernel_client.wait_for_ready(timeout=60)
            
            logger.info("Sandbox initialized and connected successfully.")
            
        except Exception as e:
            logger.error(f"Failed to start sandbox container: {e}")
            self.cleanup()
            raise e

    def _wait_for_connection_file(self, filename: str):
        target_path = os.path.join(self.host_runtime_dir, filename)
        start_time = time.time()
        while time.time() - start_time < self.timeout_secs:
            if os.path.exists(target_path):
                time.sleep(0.5) 
                return
            time.sleep(1)
            
            self.container.reload()
            if self.container.status == "exited":
                logs = self.container.logs().decode("utf-8")
                raise RuntimeError(f"Container exited prematurely.\nLogs:\n{logs}")
                
        raise TimeoutError(f"Connection file was not created within {self.timeout_secs}s.")

    def execute_code(self, code: str) -> dict:
        """
        向驻留上下文发送执行请求。
        返回值包裹： {'status': 'ok'/'error', 'stdout': '...', 'stderr': '...'}
        """
        if not self.kernel_client:
            raise RuntimeError("Kernel client is not connected.")
            
        logger.info(f"Executing code chunk (length={len(code)})")
        
        msg_id = self.kernel_client.execute(code)
        
        output_data = {"status": "unknown", "stdout": "", "stderr": "", "display_data": []}
        
        while True:
            try:
                # 获取 ZeroMQ 包装好的打印和报错
                msg = self.kernel_client.get_iopub_msg(timeout=self.timeout_secs)
            except Exception:
                output_data["status"] = "timeout"
                break
                
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
                
            msg_type = msg["header"]["msg_type"]
            content = msg["content"]
            
            if msg_type == "stream":
                if content["name"] == "stdout":
                    output_data["stdout"] += content["text"]
                elif content["name"] == "stderr":
                    output_data["stderr"] += content["text"]
            elif msg_type == "error":
                traceback_txt = "\n".join(content.get("traceback", []))
                output_data["stderr"] += f"\n{content.get('ename', '')}: {content.get('evalue', '')}\n{traceback_txt}"
            elif msg_type == "display_data":
                output_data["display_data"].append(content["data"])
            elif msg_type == "status" and content["execution_state"] == "idle":
                # 计算完毕
                break
                
        try:
            # 获取主回复查看整体状态
            reply = self.kernel_client.get_shell_msg(timeout=10)
            output_data["status"] = reply["content"]["status"]
        except Exception:
            pass
            
        return output_data

    def cleanup(self):
        """
        关闭 kernel 的所有频道，并杀掉 Docker 容器
        """
        logger.info(f"Cleaning up Sandbox {self.container_name}...")
        
        if self.kernel_client:
            try:
                self.kernel_client.stop_channels()
            except Exception:
                pass
            
        if self.container:
            try:
                self.container.reload()
                if self.container.status == "running":
                    self.container.stop(timeout=5)
                # auto_remove=True 会自动销毁
            except Exception:
                pass
        
        # 顺便清理生成的 JSON 端口凭证文件
        connection_file_path = os.path.join(self.host_runtime_dir, f"kernel-{self.session_id}.json")
        if os.path.exists(connection_file_path):
            try:
                os.remove(connection_file_path)
            except OSError:
                pass
