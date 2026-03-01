import docker
import os
try:
    client = docker.from_env()
    print("from_env Success")
except Exception as e:
    print(f"from_env Failed: {e}")
    
    # Try typical orbstack socket
    sock_path = os.path.expanduser("~/.orbstack/run/docker.sock")
    if os.path.exists(sock_path):
        print(f"Found orbstack sock at {sock_path}")
        try:
            client = docker.DockerClient(base_url=f"unix://{sock_path}")
            print("Connected via orbstack sock")
        except Exception as e2:
            print(f"orbstack connect failed: {e2}")
    else:
        print("No orbstack sock found at usual location")
