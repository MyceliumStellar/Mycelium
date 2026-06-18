from ide.backend.sandbox.docker_runner import compile_in_docker_sandbox

def compile_in_host_sandbox(source_code: str) -> dict:
    """
    Executes compilation inside the containerized Docker sandbox.
    Keeps legacy function name intact to prevent breaking backend routing imports.
    """
    return compile_in_docker_sandbox(source_code)
