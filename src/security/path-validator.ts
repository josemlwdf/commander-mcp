import path from 'node:path';
import fs from 'node:fs';

export interface ValidationResult {
  allowed: boolean;
  violations: string[];
  warning?: string;
  effectiveCwd: string;
}

export class PathValidator {
  private canonicalRoot: string;
  private isWindows: boolean;

  constructor(rootDir: string, isWindowsOverride?: boolean) {
    this.isWindows = isWindowsOverride ?? process.platform === 'win32';
    // Resolve and normalize root with appropriate platform module
    const resolved = this.isWindows ? path.win32.resolve(rootDir) : path.posix.resolve(rootDir);
    // If native platform matches, resolve real path to prevent symlink bypass
    if (this.isWindows === (process.platform === 'win32')) {
      try {
        this.canonicalRoot = fs.realpathSync.native(resolved);
      } catch {
        this.canonicalRoot = resolved;
      }
    } else {
      this.canonicalRoot = resolved;
    }
  }

  public getRoot(): string {
    return this.canonicalRoot;
  }

  /**
   * Checks if an absolute or relative path resolves strictly within the canonical root.
   */
  public isPathInside(targetPath: string, baseDir: string = this.canonicalRoot): boolean {
    if (!targetPath || targetPath.trim() === '') {
      return true;
    }

    const cleanPath = this.stripQuotes(targetPath.trim());

    // Resolve path relative to baseDir
    let resolved: string;
    if (this.isWindows) {
      resolved = path.win32.resolve(baseDir, cleanPath);
    } else {
      resolved = path.posix.resolve(baseDir, cleanPath);
    }

    // Try realpath if file exists and matches host platform to catch symlinks
    let realResolved = resolved;
    if (this.isWindows === (process.platform === 'win32')) {
      try {
        realResolved = fs.realpathSync.native(resolved);
      } catch {
        // File may not exist yet (e.g. target of write/creation), use resolved path
      }
    }

    return this.isSubpathOf(realResolved, this.canonicalRoot);
  }

  /**
   * Validates a subdirectory parameter passed by the agent.
   */
  public validateSubdir(subdir?: string): { valid: boolean; resolvedPath: string; error?: string } {
    if (!subdir || subdir.trim() === '' || subdir.trim() === '.') {
      return { valid: true, resolvedPath: this.canonicalRoot };
    }

    const cleanSubdir = this.stripQuotes(subdir.trim());
    let resolvedSubdir: string;

    if (this.isWindows) {
      resolvedSubdir = path.win32.isAbsolute(cleanSubdir)
        ? path.win32.resolve(cleanSubdir)
        : path.win32.resolve(this.canonicalRoot, cleanSubdir);
    } else {
      resolvedSubdir = path.posix.isAbsolute(cleanSubdir)
        ? path.posix.resolve(cleanSubdir)
        : path.posix.resolve(this.canonicalRoot, cleanSubdir);
    }

    if (!this.isSubpathOf(resolvedSubdir, this.canonicalRoot)) {
      return {
        valid: false,
        resolvedPath: this.canonicalRoot,
        error: `Subdirectory '${subdir}' resolves to '${resolvedSubdir}', which is outside the authorized workspace root '${this.canonicalRoot}'.`
      };
    }

    return { valid: true, resolvedPath: resolvedSubdir };
  }

  /**
   * Thoroughly analyzes a shell command string for out-of-context paths and breakout attempts.
   */
  public validateCommand(command: string, effectiveCwd: string = this.canonicalRoot): ValidationResult {
    const violations: string[] = [];

    if (!command || command.trim() === '') {
      return {
        allowed: false,
        violations: ['Empty command provided.'],
        warning: 'Command string cannot be empty.',
        effectiveCwd
      };
    }

    // Check for nested subcommands: $(...) and `...`
    const subshellRegex = /\$\(([^)]+)\)|`([^`]+)`/g;
    let subshellMatch: RegExpExecArray | null;
    while ((subshellMatch = subshellRegex.exec(command)) !== null) {
      const innerCmd = subshellMatch[1] || subshellMatch[2];
      const innerResult = this.validateCommand(innerCmd, effectiveCwd);
      if (!innerResult.allowed) {
        violations.push(...innerResult.violations.map(v => `In subshell ($(...)): ${v}`));
      }
    }

    // Tokenize command line into individual components
    const tokens = this.tokenizeCommand(command);

    for (let i = 0; i < tokens.length; i++) {
      const token = tokens[i];
      const raw = token.raw;
      const stripped = this.stripQuotes(raw);

      // Check dangerous environment variable expansions that target external system locations
      const envPattern = /(\$(?:HOME|USERPROFILE|APPDATA|LOCALAPPDATA|SYSTEMROOT|WINDIR|TMPDIR)|%[A-Za-z_]+%)/gi;
      let envMatch: RegExpExecArray | null;
      while ((envMatch = envPattern.exec(stripped)) !== null) {
        const envVar = envMatch[1];
        // Allow common safe non-path vars or inspect them
        const upperEnv = envVar.toUpperCase().replace(/[%${}]/g, '');
        if (['HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'HOMEPATH'].includes(upperEnv)) {
          violations.push(`Blocked dangerous environment variable '${envVar}' which targets paths outside the authorized workspace.`);
        }
      }

      // Check Tilde expansion (~ or ~/...)
      if (stripped === '~' || stripped.startsWith('~/') || stripped.startsWith('~\\')) {
        violations.push(`Blocked home directory path '${stripped}'. All paths must be within the workspace root.`);
      }

      // Check redirection targets (> out.txt, >> out.txt, < in.txt, 2> err.txt)
      if (['>', '>>', '<', '2>', '1>', '&>', '>|'].includes(stripped)) {
        if (i + 1 < tokens.length) {
          const target = this.stripQuotes(tokens[i + 1].raw);
          if (this.looksLikePath(target)) {
            if (!this.isPathInside(target, effectiveCwd)) {
              violations.push(`Redirection target '${target}' points outside the authorized workspace root.`);
            }
          }
        }
        continue;
      }

      // Check directory change commands (cd .. or chdir ..)
      if (token.isCommand && (stripped === 'cd' || stripped === 'chdir' || stripped === 'Set-Location' || stripped === 'Push-Location')) {
        if (i + 1 < tokens.length) {
          const dest = this.stripQuotes(tokens[i + 1].raw);
          if (!this.isPathInside(dest, effectiveCwd)) {
            violations.push(`Directory change target '${dest}' escapes the authorized workspace root.`);
          }
        }
      }

      // If token contains flags with path assignments like --output=/tmp/foo or -o=/tmp/foo
      if (stripped.startsWith('-') && stripped.includes('=')) {
        const parts = stripped.split(/=(.*)/s);
        const value = parts[1];
        if (value && this.looksLikePath(value)) {
          if (!this.isPathInside(value, effectiveCwd)) {
            violations.push(`Flag parameter path '${value}' in '${stripped}' points outside the authorized workspace root.`);
          }
        }
        continue;
      }

      // If token is a command name/binary at the beginning of a statement
      if (token.isCommand) {
        // If binary is an absolute path, verify if it's a standard system binary or inside workspace
        if (this.isExplicitAbsolutePath(stripped)) {
          if (this.isStandardSystemBinary(stripped)) {
            // Permitted system binary execution
            continue;
          } else if (!this.isPathInside(stripped, effectiveCwd)) {
            violations.push(`Command executable '${stripped}' is outside both the workspace root and standard system binary directories.`);
          }
        }
        continue;
      }

      // Check candidate path arguments
      if (this.looksLikePath(stripped)) {
        if (!this.isPathInside(stripped, effectiveCwd)) {
          violations.push(`Path argument '${stripped}' points outside the authorized workspace root.`);
        }
      }
    }

    if (violations.length > 0) {
      const warning = [
        `[SECURITY ALERT - COMMAND BLOCKED]`,
        `The command was rejected because it attempts to access files or directories outside your authorized context.`,
        `Authorized Workspace Root: ${this.canonicalRoot}`,
        `Current Effective Directory: ${effectiveCwd}`,
        `Detected Security Violations:`,
        ...violations.map(v => `  - ${v}`),
        ``,
        `Remediation:`,
        `  - Do not use parent directory traversals ('../') that leave the workspace.`,
        `  - Do not reference absolute paths like '/etc', '/var', '/tmp', 'C:\\Windows', or user homes ('~').`,
        `  - Keep all file reads, writes, and script targets strictly relative to or inside '${this.canonicalRoot}'.`
      ].join('\n');

      return {
        allowed: false,
        violations,
        warning,
        effectiveCwd
      };
    }

    return {
      allowed: true,
      violations: [],
      effectiveCwd
    };
  }

  /**
   * Checks whether childPath is strictly contained inside parentPath (or is equal to it).
   */
  private isSubpathOf(childPath: string, parentPath: string): boolean {
    let p = parentPath;
    let c = childPath;

    if (this.isWindows) {
      // Normalize Windows slashes and case
      p = path.win32.normalize(p).toLowerCase();
      c = path.win32.normalize(c).toLowerCase();

      // Check drive letter match
      const pDrive = path.win32.parse(p).root.toLowerCase();
      const cDrive = path.win32.parse(c).root.toLowerCase();
      if (pDrive !== cDrive) {
        return false;
      }

      const rel = path.win32.relative(p, c);
      return rel === '' || (!rel.startsWith('..') && !path.win32.isAbsolute(rel));
    } else {
      p = path.posix.normalize(p);
      c = path.posix.normalize(c);

      const rel = path.posix.relative(p, c);
      return rel === '' || (!rel.startsWith('..') && !path.posix.isAbsolute(rel));
    }
  }

  private isExplicitAbsolutePath(p: string): boolean {
    if (this.isWindows) {
      return path.win32.isAbsolute(p) || /^[A-Za-z]:[/\\]/.test(p) || p.startsWith('\\\\');
    }
    return path.posix.isAbsolute(p);
  }

  /**
   * Determines if a token looks like a file system path rather than a simple CLI flag or word.
   */
  private looksLikePath(token: string): boolean {
    if (!token || token.length === 0) return false;
    // Skip simple CLI flags like -l, --all, -rf
    if (token.startsWith('-') && !token.includes('/') && !token.includes('\\')) {
      return false;
    }
    // Explicit traversal patterns
    if (token === '..' || token.startsWith('../') || token.startsWith('..\\') || token.includes('/../') || token.includes('\\..\\')) {
      return true;
    }
    // Starts with dot relative
    if (token.startsWith('./') || token.startsWith('.\\')) {
      return true;
    }
    // POSIX absolute path
    if (token.startsWith('/')) {
      return true;
    }
    // Windows absolute path (drive letter or UNC)
    if (/^[A-Za-z]:[/\\]/.test(token) || token.startsWith('\\\\') || token.startsWith('\\')) {
      return true;
    }
    // Contains slashes
    if (token.includes('/') || token.includes('\\')) {
      return true;
    }
    return false;
  }

  /**
   * Whitelist standard system binaries when called as the executable name.
   */
  private isStandardSystemBinary(execPath: string): boolean {
    const clean = execPath.replace(/\\/g, '/');
    const allowedPrefixes = [
      '/bin/',
      '/usr/bin/',
      '/usr/local/bin/',
      '/sbin/',
      '/usr/sbin/',
      'c:/windows/system32/',
      'c:/windows/',
      'c:/program files/'
    ];
    const lower = clean.toLowerCase();
    return allowedPrefixes.some(prefix => lower.startsWith(prefix));
  }

  private stripQuotes(str: string): string {
    if ((str.startsWith('"') && str.endsWith('"')) || (str.startsWith("'") && str.endsWith("'"))) {
      return str.slice(1, -1);
    }
    return str;
  }

  /**
   * Tokenizes a command line respecting quotes and command chaining operators.
   */
  private tokenizeCommand(command: string): Array<{ raw: string; isCommand: boolean }> {
    const tokens: Array<{ raw: string; isCommand: boolean }> = [];
    let current = '';
    let inSingleQuote = false;
    let inDoubleQuote = false;
    let isNextCommand = true;

    for (let i = 0; i < command.length; i++) {
      const char = command[i];
      const nextChar = command[i + 1];

      if (char === "'" && !inDoubleQuote) {
        inSingleQuote = !inSingleQuote;
        current += char;
        continue;
      }

      if (char === '"' && !inSingleQuote) {
        inDoubleQuote = !inDoubleQuote;
        current += char;
        continue;
      }

      if (!inSingleQuote && !inDoubleQuote) {
        // Chaining operators reset command position: &&, ||, |, ;, &
        if (
          (char === '&' && nextChar === '&') ||
          (char === '|' && nextChar === '|')
        ) {
          if (current.trim()) {
            tokens.push({ raw: current.trim(), isCommand: isNextCommand });
            current = '';
          }
          tokens.push({ raw: char + nextChar, isCommand: false });
          i++; // skip next char
          isNextCommand = true;
          continue;
        }

        if (char === '|' || char === ';' || char === '&' || char === '\n') {
          if (current.trim()) {
            tokens.push({ raw: current.trim(), isCommand: isNextCommand });
            current = '';
          }
          tokens.push({ raw: char, isCommand: false });
          isNextCommand = true;
          continue;
        }

        if (/\s/.test(char)) {
          if (current.trim()) {
            tokens.push({ raw: current.trim(), isCommand: isNextCommand });
            current = '';
            isNextCommand = false;
          }
          continue;
        }
      }

      current += char;
    }

    if (current.trim()) {
      tokens.push({ raw: current.trim(), isCommand: isNextCommand });
    }

    return tokens;
  }
}
