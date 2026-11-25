import sys
import asyncio
import tempfile
import subprocess
import os

sys.path.insert(0, '.')

from orchestrator.orchestrator import Orchestrator, AgentContext, SubagentHandle

async def test():
    with tempfile.TemporaryDirectory() as tmp:
        import subprocess
        subprocess.run(['git', 'init', '-b', 'main'], cwd=tmp, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=tmp, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp, check=True)
        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'init'], cwd=tmp, check=True, capture_output=True)

        orch = Orchestrator(project_root=tmp)
        await orch.initialize()

        # Test spawn with actual execution
        handle = await orch.spawn_subagent(
            task='Create a simple hello.py file that prints hello world',
            role='CODER',
            worktree=True,
        )
        print(f'Spawned: {handle.agent_id}')

        # Wait for completion
        result = await handle.wait()
        print(f'Result: {result}')
        print(f'Success: {result.success}')
        print(f'Output: {result.output}')
        print(f'Error: {result.error}')

        # Check worktree for created file
        import os
        wt = handle.context.worktree_path
        print(f'Worktree files: {os.listdir(wt)}')

        await orch.cleanup()
        print('Test passed!')

if __name__ == "__main__":
    asyncio.run(test())