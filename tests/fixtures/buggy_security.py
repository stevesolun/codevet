import subprocess


def run_command(user_input):
    # Bug: Command injection
    return subprocess.run(f"echo {user_input}", shell=True, capture_output=True)
