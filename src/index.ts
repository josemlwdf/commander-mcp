#!/usr/bin/env node

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import path from 'node:path';
import { detectEnvironment } from './security/environment.js';
import { PathValidator } from './security/path-validator.js';
import { ProcessRunner } from './executor/process-runner.js';
import {
  buildCommandToolDefinition,
  buildContextToolDefinition,
  handleExecuteCommand,
  handleGetWorkspaceContext,
  ExecuteCommandArgs,
} from './tools/command-tool.js';

function parseArgs(): { rootDir: string; shellOverride?: string } {
  const args = process.argv.slice(2);
  let rootDir = process.cwd();
  let shellOverride: string | undefined;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--root' || arg === '-r') {
      if (i + 1 < args.length) {
        rootDir = path.resolve(args[i + 1]);
        i++;
      }
    } else if (arg === '--shell' || arg === '-s') {
      if (i + 1 < args.length) {
        shellOverride = args[i + 1];
        i++;
      }
    } else if (arg === '--help' || arg === '-h') {
      console.error(`
Commander mcp - Secure Context-Confined Command Execution MCP Server

Usage:
  commander-mcp [options]

Options:
  -r, --root <path>     Authorized workspace root directory (default: current working directory)
  -s, --shell <path>    Override default shell executable (e.g. powershell.exe, bash)
  -h, --help            Show this help message

Environment Variables:
  COMMANDER_ROOT        Alternative way to specify authorized workspace root directory
      `);
      process.exit(0);
    }
  }

  if (process.env.COMMANDER_ROOT && rootDir === process.cwd()) {
    rootDir = path.resolve(process.env.COMMANDER_ROOT);
  }

  return { rootDir, shellOverride };
}

async function main() {
  const { rootDir, shellOverride } = parseArgs();
  const envInfo = detectEnvironment();

  if (shellOverride) {
    envInfo.defaultShell = shellOverride;
  }

  const validator = new PathValidator(rootDir);
  const runner = new ProcessRunner(envInfo);

  // Note: logs go to stderr to prevent corrupting MCP stdio JSON-RPC protocol
  console.error(`[commander-mcp] Starting server...`);
  console.error(`[commander-mcp] OS: ${envInfo.osName} (${envInfo.architecture})`);
  console.error(`[commander-mcp] Shell: ${envInfo.defaultShell}`);
  console.error(`[commander-mcp] Authorized Root: ${validator.getRoot()}`);

  const server = new Server(
    {
      name: 'commander-mcp',
      version: '1.0.0',
    },
    {
      capabilities: {
        tools: {},
      },
    }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
      tools: [
        buildCommandToolDefinition(validator, envInfo),
        buildContextToolDefinition(validator, envInfo),
      ],
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: toolArgs } = request.params;

    if (name === 'execute_command') {
      const args = (toolArgs || {}) as unknown as ExecuteCommandArgs;
      return await handleExecuteCommand(args, validator, runner);
    }

    if (name === 'get_workspace_context') {
      return handleGetWorkspaceContext(validator, envInfo);
    }

    return {
      isError: true,
      content: [
        {
          type: 'text',
          text: `Unknown tool requested: '${name}'`,
        },
      ],
    };
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`[commander-mcp] Server connected via stdio transport. Ready for requests.`);
}

main().catch((err) => {
  console.error(`[commander-mcp] Fatal startup error:`, err);
  process.exit(1);
});

