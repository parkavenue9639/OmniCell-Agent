import logging
import os
import sys

# 添加 src 到路径
sys.path.append(os.path.join(os.path.dirname(__file__), '../../src'))

from omnicell_agent.sandbox.docker_manager import DockerJupyterSandbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_direct_test():
    # 使用公开的 jupyter minimal-notebook 或者临时命令，避免 build 导致网络死锁
    # 为了保证能最快跑通，我们重写一下 Sandbox 运行这部分的原型注入方式（仅用于这里的特供测试）
    sandbox = DockerJupyterSandbox(image_name="python:3.10-slim", timeout_secs=120)
    
    # 我们覆盖一下 sandbox 的 cmd，让它在一次 run 里装包并启动内核
    sandbox._original_start = sandbox.start
    
    def my_custom_start():
        logger.info("Starting test Sandbox with dynamic installation...")
        volumes = {
            sandbox.host_data_dir: {'bind': sandbox.container_data_dir, 'mode': 'rw'},
            sandbox.host_runtime_dir: {'bind': sandbox.container_runtime_dir, 'mode': 'rw'}
        }
        connection_file_name = f"kernel-{sandbox.session_id}.json"
        
        # 抛弃 entrypoint，强行写 sh -c
        cmd = [
            "sh", "-c", 
            f"pip install ipykernel -i https://pypi.tuna.tsinghua.edu.cn/simple && python -m ipykernel_launcher -f {sandbox.container_runtime_dir}/{connection_file_name}"
        ]
        
        sandbox.container = sandbox.docker_client.containers.run(
            image=sandbox.image_name,
            name=sandbox.container_name,
            command=cmd,
            volumes=volumes,
            detach=True,
            network_mode="host", 
            auto_remove=True
        )
        sandbox._wait_for_connection_file(connection_file_name)
        connection_file_path = os.path.join(sandbox.host_runtime_dir, connection_file_name)
        
        from jupyter_client import BlockingKernelClient
        sandbox.kernel_client = BlockingKernelClient(connection_file=connection_file_path)
        sandbox.kernel_client.load_connection_file()
        sandbox.kernel_client.start_channels()
        sandbox.kernel_client.wait_for_ready(timeout=60)
        logger.info("Sandbox initialized and connected successfully.")

    sandbox.start = my_custom_start
    
    try:
        sandbox.start()
        res1 = sandbox.execute_code("x = 100\nprint(f'x is {x}')")
        assert 'x is 100' in res1['stdout'], f"Unexpected stdout: {res1['stdout']}"
        res2 = sandbox.execute_code("y = x + 50\nprint(f'y is {y}')")
        assert 'y is 150' in res2['stdout']
        logger.info("-" * 40)
        logger.info("✅ SUCCESS: Docker Sandbox Context is PERSISTENT!")
        logger.info("-" * 40)
    finally:
        sandbox.cleanup()

if __name__ == '__main__':
    run_direct_test()
