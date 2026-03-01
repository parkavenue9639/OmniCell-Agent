import logging
from omnicell_agent.sandbox.docker_manager import DockerJupyterSandbox

logging.basicConfig(level=logging.INFO)

def run_test():
    # 为了快速测试不用拉几十GB的生信镜像，用官方的 python:3.9 测试
    # 但由于它内部可能没有安装 ipykernel，我们需要确保运行环境有这个。
    # 更简单的方法：用 python:3.10-slim 然后顺带 pip install ipykernel。
    # 这只是个原型演示，如果本地没有 python:3.9 会 pull很久。
    pass

if __name__ == '__main__':
    run_test()
