import { PathValidator } from '../security/path-validator.js';
import { ProcessRunner } from '../executor/process-runner.js';
import { EnvironmentInfo, buildDynamicAgentHint } from '../security/environment.js';

export interface ExecuteCommandArgs {
  command: string;
  subdir?: string;
  timeout_ms?: number;
}

export function buildCommandToolDefinition(
  validator: PathValidator,
  envInfo: EnvironmentInfo
) {
  const root = validator.getRoot();
  const description = buildDynamicAgentHint(root, envInfo);

  return {
    name: 'execute_command',
    description,
    inputSchema: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          description: 'Shell command to execute.'
        },
        subdir: {
          type: 'string',
          description: 'Optional relative subdirectory inside workspace.'
        },
        timeout_ms: {
          type: 'number',
          description: 'Optional timeout in milliseconds (default: 60000).'
        }
      },
      required: ['command']
    }
  };
}

export async function handleExecuteCommand(
  args: ExecuteCommandArgs,
  validator: PathValidator,
  runner: ProcessRunner
) {
  const { command, subdir, timeout_ms } = args;

  // 1. Validate subdirectory
  const subdirCheck = validator.validateSubdir(subdir);
  if (!subdirCheck.valid) {
    return {
      isError: true,
      content: [
        {
          type: 'text',
          text: `[SECURITY ALERT - SUBDIRECTORY REJECTED]\n${subdirCheck.error}`
        }
      ]
    };
  }

  // 2. Validate command for out-of-context path violations
  const validation = validator.validateCommand(command, subdirCheck.resolvedPath);
  if (!validation.allowed) {
    return {
      isError: true,
      content: [
        {
          type: 'text',
          text: validation.warning || 'Command blocked due to out-of-context path violation.'
        }
      ]
    };
  }

  // 3. Execute command in validated directory
  const result = await runner.execute(command, {
    cwd: subdirCheck.resolvedPath,
    timeoutMs: timeout_ms
  });

  const responseLines: string[] = [
    `[COMMAND EXECUTED]`,
    `Directory: ${subdirCheck.resolvedPath}`,
    `Exit Code: ${result.exitCode ?? 'none'}`,
    `Duration: ${result.durationMs}ms`,
    `Timed Out: ${result.timedOut}`
  ];

  if (result.stdout.trim()) {
    responseLines.push(`\n--- STDOUT ---\n${result.stdout.trimEnd()}`);
  }

  if (result.stderr.trim()) {
    responseLines.push(`\n--- STDERR ---\n${result.stderr.trimEnd()}`);
  }

  if (!result.stdout.trim() && !result.stderr.trim()) {
    responseLines.push('\n(Command produced no standard output or error output)');
  }

  return {
    isError: result.exitCode !== 0 || result.timedOut,
    content: [
      {
        type: 'text',
        text: responseLines.join('\n')
      }
    ]
  };
}
