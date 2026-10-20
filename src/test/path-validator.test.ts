import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { PathValidator } from '../security/path-validator.js';

test('PathValidator - POSIX environment', async (t) => {
  const root = '/workspace/my-project';
  const validator = new PathValidator(root, false);

  await t.test('allows normal in-context commands', () => {
    const validCommands = [
      'npm install',
      'git status',
      'ls -la',
      'cat src/index.ts',
      'node dist/index.js',
      'mkdir -p ./build/output',
      'echo "hello world" > ./output.txt',
      'python3 -m unittest discover tests',
      'grep -rn "TODO" ./'
    ];

    for (const cmd of validCommands) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, true, `Expected command to be allowed: "${cmd}"`);
      assert.equal(res.violations.length, 0);
    }
  });

  await t.test('blocks parent directory traversal', () => {
    const dangerousCommands = [
      'cat ../secret.txt',
      'cd .. && ls',
      'cat ./src/../../parent.txt',
      'echo "leak" > ../leak.txt',
      'diff file.txt ../other.txt'
    ];

    for (const cmd of dangerousCommands) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, false, `Expected command to be BLOCKED: "${cmd}"`);
      assert.ok(res.violations.length > 0);
      assert.ok(res.warning?.includes('SECURITY ALERT'));
    }
  });

  await t.test('blocks absolute system paths', () => {
    const dangerousCommands = [
      'cat /etc/passwd',
      'ls /var/log',
      'echo "malicious" > /tmp/evil.sh',
      'head -n 20 /etc/shadow',
      'tail -f /var/log/auth.log'
    ];

    for (const cmd of dangerousCommands) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, false, `Expected command to be BLOCKED: "${cmd}"`);
      assert.ok(res.violations.length > 0);
      assert.ok(res.warning?.includes('SECURITY ALERT'));
    }
  });

  await t.test('blocks subshell breakouts', () => {
    const subshellCmds = [
      'echo $(cat /etc/passwd)',
      'echo `cat /etc/shadow`',
      'var=$(cat ../outside.txt)'
    ];

    for (const cmd of subshellCmds) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, false, `Expected subshell command to be BLOCKED: "${cmd}"`);
      assert.ok(res.violations.length > 0);
    }
  });

  await t.test('blocks home directory and dangerous environment variables', () => {
    const envCmds = [
      'cat ~/.ssh/id_rsa',
      'cat $HOME/.bashrc',
      'ls ~/documents',
      'tar -czf backup.tar.gz $USERPROFILE'
    ];

    for (const cmd of envCmds) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, false, `Expected env command to be BLOCKED: "${cmd}"`);
      assert.ok(res.violations.length > 0);
    }
  });

  await t.test('validates subdirectories correctly', () => {
    const valid = validator.validateSubdir('src');
    assert.equal(valid.valid, true);
    assert.equal(valid.resolvedPath, '/workspace/my-project/src');

    const invalid = validator.validateSubdir('../../etc');
    assert.equal(invalid.valid, false);
    assert.ok(invalid.error?.includes('outside the authorized workspace root'));

    const invalidAbs = validator.validateSubdir('/var/log');
    assert.equal(invalidAbs.valid, false);
  });
});

test('PathValidator - Windows environment simulation', async (t) => {
  const root = 'C:\\Users\\Developer\\project';
  const validator = new PathValidator(root, true);

  await t.test('allows valid Windows workspace commands', () => {
    const validCommands = [
      'dir',
      'Get-ChildItem -Path .\\src',
      'type .\\package.json',
      'node .\\dist\\index.js',
      'npm test',
      'git status'
    ];

    for (const cmd of validCommands) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, true, `Expected Windows command to be allowed: "${cmd}"`);
    }
  });

  await t.test('blocks Windows path breakouts', () => {
    const blockedCommands = [
      'type C:\\Windows\\System32\\drivers\\etc\\hosts',
      'dir C:\\Users\\Administrator',
      'dir D:\\other_drive\\data',
      'type ..\\..\\passwords.txt',
      'echo evil > C:\\Windows\\temp.txt',
      'dir %USERPROFILE%\\secrets',
      'Get-Content %APPDATA%\\conf.json'
    ];

    for (const cmd of blockedCommands) {
      const res = validator.validateCommand(cmd, root);
      assert.equal(res.allowed, false, `Expected Windows command to be BLOCKED: "${cmd}"`);
      assert.ok(res.violations.length > 0);
      assert.ok(res.warning?.includes('SECURITY ALERT'));
    }
  });

  await t.test('blocks Windows cross-drive access', () => {
    const crossDriveSubdir = validator.validateSubdir('D:\\other_drive');
    assert.equal(crossDriveSubdir.valid, false);
  });
});
