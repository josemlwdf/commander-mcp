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

  await t.test('lists only execute_command with reduced description', async () => {
    const toolsResult = await client.listTools();
    assert.equal(toolsResult.tools.length, 1);

    const execTool = toolsResult.tools[0];
    assert.equal(execTool.name, 'execute_command');

    // Verify concise description contains Root and confinement hints without verbose boilerplate
    assert.ok(execTool.description?.includes(workspaceRoot));
    assert.ok(execTool.description?.includes('strictly confined'));
    assert.ok(!execTool.description?.includes('RUNTIME OPERATING SYSTEM & WORKSPACE NOTICE'));
  });

  await t.test('rejects unknown tools such as get_workspace_context', async () => {
    const res = await client.callTool({
      name: 'get_workspace_context',
      arguments: {},
    });

    assert.equal(res.isError, true);
    const content = res.content as Array<{ type: string; text: string }>;
    assert.ok(content[0].text.includes('Unknown tool requested'));
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
