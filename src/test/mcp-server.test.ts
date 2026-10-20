import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

test('MCP Server End-to-End', async (t) => {
  const serverPath = path.resolve(process.cwd(), 'dist/index.js');
  const workspaceRoot = process.cwd();

  const transport = new StdioClientTransport({
    command: 'node',
    args: [serverPath, '--root', workspaceRoot],
  });

  const client = new Client(
    {
      name: 'test-mcp-client',
      version: '1.0.0',
    },
    {
      capabilities: {},
    }
  );

  await client.connect(transport);

  await t.test('lists tools with dynamic OS hints and description', async () => {
    const toolsResult = await client.listTools();
    assert.ok(toolsResult.tools.length >= 2);

    const execTool = toolsResult.tools.find((tool) => tool.name === 'execute_command');
    assert.ok(execTool, 'execute_command tool must exist');

    // Verify dynamic description contains OS and Root hints
    assert.ok(execTool.description?.includes('RUNTIME OPERATING SYSTEM & WORKSPACE NOTICE'));
    assert.ok(execTool.description?.includes('Authorized Root'));
    assert.ok(execTool.description?.includes(workspaceRoot));
    assert.ok(execTool.description?.includes('CONTEXT CONFINEMENT'));
  });

  await t.test('calls get_workspace_context successfully', async () => {
    const res = await client.callTool({
      name: 'get_workspace_context',
      arguments: {},
    });

    assert.ok(!res.isError);
    const content = res.content as Array<{ type: string; text: string }>;
    assert.ok(content.length > 0);
    const parsed = JSON.parse(content[0].text);
    assert.ok(parsed.operatingSystem);
    assert.ok(parsed.authorizedRoot);
    assert.equal(parsed.authorizedRoot, workspaceRoot);
  });

  await t.test('executes allowed in-context command', async () => {
    const res = await client.callTool({
      name: 'execute_command',
      arguments: {
        command: 'echo "mcp-e2e-ok"',
      },
    });

    assert.ok(!res.isError);
    const content = res.content as Array<{ type: string; text: string }>;
    assert.ok(content[0].text.includes('mcp-e2e-ok'));
  });

  await t.test('blocks out-of-context command and returns warning', async () => {
    const res = await client.callTool({
      name: 'execute_command',
      arguments: {
        command: 'cat /etc/passwd',
      },
    });

    assert.equal(res.isError, true);
    const content = res.content as Array<{ type: string; text: string }>;
    assert.ok(content[0].text.includes('SECURITY ALERT - COMMAND BLOCKED'));
    assert.ok(content[0].text.includes('/etc/passwd'));
  });

  await t.test('blocks out-of-context directory traversal command', async () => {
    const res = await client.callTool({
      name: 'execute_command',
      arguments: {
        command: 'cat ../outside.txt',
      },
    });

    assert.equal(res.isError, true);
    const content = res.content as Array<{ type: string; text: string }>;
    assert.ok(content[0].text.includes('SECURITY ALERT - COMMAND BLOCKED'));
  });

  await client.close();
});
