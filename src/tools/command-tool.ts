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
  const dynamicHint = buildDynamicAgentHint(root, envInfo);

  const description = [
    `Execute system commands strictly confined within the authorized project directory.`,
    ``,
    dynamicHint,
    ``,
    `IMPORTANT USAGE RULES:`,
    `- All file paths must be relative to the authorized root or within it.`,
    `- Any command referencing out-of-context paths (e.g. '../', '/etc', 'C:\\Windows', '~') will be rejected.`,
    `- Use native shell syntax for ${envInfo.osName} (${envInfo.defaultShell}).`
  ].join('\n');

  return {
    name: 'execute_command',
    description,
    inputSchema: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          description: `The system shell command to execute in ${envInfo.osName} (${envInfo.defaultShell}). Must not reference paths outside the workspace.`
        },
        subdir: {
          type: 'string',
          description: `Optional relative path to a subdirectory inside '${root}' to run the command in.`
        },
        timeout_ms: {
          type: 'number',
          description: 'Optional execution timeout in milliseconds. Default: 60000 (60 seconds).'
        }
      },
      required: ['command']
    }
  };
}

export function buildContextToolDefinition(
  validator: PathValidator,
  envInfo: EnvironmentInfo
) {
  return {
    name: 'get_workspace_context',
    description: 'Inspect the authorized workspace root, OS details, default shell, and confinement rules.',
    inputSchema: {
      type: 'object',
      properties: {}
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

export function handleGetWorkspaceContext(
  validator: PathValidator,
  envInfo: EnvironmentInfo
) {
  const info = {
    operatingSystem: envInfo.osName,
    architecture: envInfo.architecture,
    defaultShell: envInfo.defaultShell,
    pathSeparator: envInfo.pathSeparator,
    authorizedRoot: validator.getRoot(),
    guidelines: envInfo.shellSyntaxTips
  };

  return {
    isError: false,
    content: [
      {
        type: 'text',
        text: JSON.stringify(info, null, 2)
      }
    ]
  };
}
