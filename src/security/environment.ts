import os from 'node:os';
import path from 'node:path';

export interface EnvironmentInfo {
  platform: NodeJS.Platform;
  osName: string;
  architecture: string;
  defaultShell: string;
  shellArgs: string[];
  pathSeparator: string;
  homeDir: string;
  isWindows: boolean;
  shellSyntaxTips: string[];
}

export function detectEnvironment(overridePlatform?: NodeJS.Platform): EnvironmentInfo {
  const platform = overridePlatform ?? process.platform;
  const isWindows = platform === 'win32';
  const architecture = os.arch();
  const homeDir = os.homedir();

  let osName = 'Linux';
  let defaultShell = '/bin/bash';
  let shellArgs = ['-c'];
  let shellSyntaxTips: string[] = [];

  if (isWindows) {
    osName = 'Windows';
    defaultShell = process.env.COMSPEC ? 'powershell.exe' : 'powershell.exe';
    shellArgs = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command'];
    shellSyntaxTips = [
      'Target OS is Windows. Generate PowerShell-compatible commands.',
      'Use backslashes (\\) or forward slashes (/) for relative paths inside the workspace.',
      'Use PowerShell cmdlets or Windows utilities (e.g. `dir` or `Get-ChildItem`, `type` or `Get-Content`, `Select-String`).',
      'For environment variables, use `$env:VARIABLE_NAME`.',
      'Do NOT use POSIX root paths like `/etc` or `/var` or drive roots like `C:\\` outside the workspace.',
      'All paths must remain strictly within the current workspace root or its subdirectories.'
    ];
  } else if (platform === 'darwin') {
    osName = 'macOS';
    defaultShell = process.env.SHELL || '/bin/zsh';
    shellArgs = ['-c'];
    shellSyntaxTips = [
      'Target OS is macOS (UNIX/Darwin). Generate POSIX/zsh/bash-compatible commands.',
      'Use forward slashes (/) for paths.',
      'Standard POSIX utilities are available (`ls`, `cat`, `grep`, `find`, `curl`).',
      'Do NOT reference system directories like `/etc`, `/var`, `/tmp`, or `~` outside the workspace.',
      'All paths must remain strictly within the current workspace root or its subdirectories.'
    ];
  } else {
    osName = 'Linux';
    defaultShell = process.env.SHELL || '/bin/bash';
    shellArgs = ['-c'];
    shellSyntaxTips = [
      'Target OS is Linux. Generate POSIX/bash-compatible commands.',
      'Use forward slashes (/) for paths.',
      'Standard Linux utilities are available (`ls`, `cat`, `grep`, `find`, `sed`, `awk`).',
      'Do NOT reference system directories like `/etc`, `/var`, `/tmp`, `/root`, or `~` outside the workspace.',
      'All paths must remain strictly within the current workspace root or its subdirectories.'
    ];
  }

  return {
    platform,
    osName,
    architecture,
    defaultShell,
    shellArgs,
    pathSeparator: isWindows ? '\\' : '/',
    homeDir,
    isWindows,
    shellSyntaxTips,
  };
}

export function buildDynamicAgentHint(allowedRoot: string, env: EnvironmentInfo): string {
  return [
    `=== RUNTIME OPERATING SYSTEM & WORKSPACE NOTICE ===`,
    `Operating System: ${env.osName} (${env.architecture})`,
    `Default Shell: ${env.defaultShell}`,
    `Path Separator: '${env.pathSeparator}'`,
    `Current Authorized Root: ${allowedRoot}`,
    ``,
    `=== AGENT CONSTRAINTS & SECURITY POLICY ===`,
    `1. CONTEXT CONFINEMENT: You are ONLY authorized to access files and subdirectories inside: "${allowedRoot}".`,
    `2. STRICT OUT-OF-CONTEXT BLOCK: Any command referencing paths outside this directory (e.g., parent traversals '../', absolute system paths, home directory '~' or '$HOME', or root drives) will be REJECTED AND BLOCKED.`,
    `3. OS SYNTAX TIPS:`,
    ...env.shellSyntaxTips.map(tip => `   - ${tip}`),
    `====================================================`
  ].join('\n');
}

