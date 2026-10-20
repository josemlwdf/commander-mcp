import test from 'node:test';
import assert from 'node:assert/strict';
import { detectEnvironment } from '../security/environment.js';
import { ProcessRunner } from '../executor/process-runner.js';

test('ProcessRunner execution', async (t) => {
  const env = detectEnvironment();
  const runner = new ProcessRunner(env);

  await t.test('executes simple echo command successfully', async () => {
    const res = await runner.execute('echo "mcp-test-runner"', { cwd: process.cwd() });
    assert.equal(res.exitCode, 0);
    assert.equal(res.timedOut, false);
    assert.ok(res.stdout.includes('mcp-test-runner'));
  });

  await t.test('captures non-zero exit code', async () => {
    const res = await runner.execute('exit 42', { cwd: process.cwd() });
    assert.equal(res.exitCode, 42);
  });

  await t.test('handles timeout cleanly', async () => {
    // 2 second sleep with 300ms timeout
    const res = await runner.execute('sleep 2', {
      cwd: process.cwd(),
      timeoutMs: 300
    });
    assert.equal(res.timedOut, true);
    assert.ok(res.stderr.includes('[TIMEOUT]'));
  });
});

