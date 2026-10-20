import { spawn, ChildProcess } from 'node:child_process';
import { EnvironmentInfo } from '../security/environment.js';

export interface ExecutionOptions {
  cwd: string;
  timeoutMs?: number;
  env?: NodeJS.ProcessEnv;
  maxOutputBytes?: number;
}

export interface ExecutionResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut: boolean;
  durationMs: number;
}

export class ProcessRunner {
  private envInfo: EnvironmentInfo;
  private defaultTimeoutMs: number;
  private defaultMaxOutputBytes: number;

  constructor(envInfo: EnvironmentInfo, defaultTimeoutMs = 60000, defaultMaxOutputBytes = 1024 * 1024) {
    this.envInfo = envInfo;
    this.defaultTimeoutMs = defaultTimeoutMs;
    this.defaultMaxOutputBytes = defaultMaxOutputBytes;
  }

  public async execute(command: string, options: ExecutionOptions): Promise<ExecutionResult> {
    const startTime = Date.now();
    const timeoutMs = options.timeoutMs ?? this.defaultTimeoutMs;
    const maxBytes = options.maxOutputBytes ?? this.defaultMaxOutputBytes;

    const spawnArgs = [...this.envInfo.shellArgs, command];
    const spawnEnv: NodeJS.ProcessEnv = {
      ...process.env,
      ...options.env,
      PWD: options.cwd,
      CI: 'true', // Hints tools to avoid interactive prompts
      DEBIAN_FRONTEND: 'noninteractive'
    };

    return new Promise((resolve) => {
      let stdout = '';
      let stderr = '';
      let timedOut = false;
      let stdoutTruncated = false;
      let stderrTruncated = false;

      let child: ChildProcess;
      try {
        child = spawn(this.envInfo.defaultShell, spawnArgs, {
          cwd: options.cwd,
          env: spawnEnv,
          stdio: ['ignore', 'pipe', 'pipe'],
          windowsHide: true,
        });
      } catch (err: any) {
        resolve({
          stdout: '',
          stderr: `Failed to spawn shell (${this.envInfo.defaultShell}): ${err.message}`,
          exitCode: 1,
          timedOut: false,
          durationMs: Date.now() - startTime,
        });
        return;
      }

      const timer = setTimeout(() => {
        timedOut = true;
        this.killProcess(child);
      }, timeoutMs);

      child.stdout?.on('data', (data: Buffer) => {
        if (stdout.length < maxBytes) {
          stdout += data.toString('utf-8');
          if (stdout.length >= maxBytes && !stdoutTruncated) {
            stdout += '\n... [STDOUT TRUNCATED: Exceeded 1MB limit] ...\n';
            stdoutTruncated = true;
          }
        }
      });

      child.stderr?.on('data', (data: Buffer) => {
        if (stderr.length < maxBytes) {
          stderr += data.toString('utf-8');
          if (stderr.length >= maxBytes && !stderrTruncated) {
            stderr += '\n... [STDERR TRUNCATED: Exceeded 1MB limit] ...\n';
            stderrTruncated = true;
          }
        }
      });

      child.on('error', (err) => {
        clearTimeout(timer);
        resolve({
          stdout,
          stderr: stderr ? `${stderr}\n${err.message}` : err.message,
          exitCode: 1,
          timedOut: false,
          durationMs: Date.now() - startTime,
        });
      });

      child.on('close', (code) => {
        clearTimeout(timer);
        const durationMs = Date.now() - startTime;
        if (timedOut) {
          stderr += `\n[TIMEOUT] Execution exceeded timeout limit of ${timeoutMs}ms and was killed.`;
        }
        resolve({
          stdout,
          stderr,
          exitCode: timedOut ? 124 : code,
          timedOut,
          durationMs,
        });
      });
    });
  }

  private killProcess(child: ChildProcess): void {
    try {
      if (this.envInfo.isWindows && child.pid) {
        // Windows tree-kill via taskkill
        spawn('taskkill', ['/pid', child.pid.toString(), '/T', '/F']);
      } else if (child.pid) {
        child.kill('SIGTERM');
        setTimeout(() => {
          try {
            if (!child.killed) {
              child.kill('SIGKILL');
            }
          } catch {
            // Already dead
          }
        }, 2000);
      }
    } catch {
      // Ignore cleanup error
    }
  }
}

