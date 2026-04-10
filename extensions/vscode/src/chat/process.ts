import { ChildProcess, spawn } from 'child_process';
import * as vscode from 'vscode';

export class LlmcodeProcess {
  private proc: ChildProcess | null = null;
  private _port: number | null = null;
  private _ready = false;

  get port(): number | null {
    return this._port;
  }

  get ready(): boolean {
    return this._ready;
  }

  async start(pythonPath: string): Promise<number> {
    const cmd = pythonPath || 'llmcode';
    const workspaceFolders = vscode.workspace.workspaceFolders;
    const cwd = workspaceFolders?.[0]?.uri.fsPath ?? process.cwd();

    return new Promise<number>((resolve, reject) => {
      const args = ['--serve', '--port', '0'];
      const isInterpreter = cmd.endsWith('python') || cmd.endsWith('python3');
      const spawnArgs = isInterpreter ? ['-m', 'llm_code', ...args] : args;

      this.proc = spawn(cmd, spawnArgs, {
        cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env },
      });

      const timeout = setTimeout(() => {
        reject(new Error('llmcode --serve did not report port within 15s'));
      }, 15_000);

      this.proc.stdout?.on('data', (chunk: Buffer) => {
        const line = chunk.toString();
        const match = line.match(/listening on ws:\/\/[^:]+:(\d+)/);
        if (match && !this._ready) {
          this._port = parseInt(match[1], 10);
          this._ready = true;
          clearTimeout(timeout);
          resolve(this._port);
        }
      });

      this.proc.stderr?.on('data', (chunk: Buffer) => {
        const line = chunk.toString().trim();
        if (line) {
          vscode.window.showWarningMessage(`llmcode: ${line.slice(0, 200)}`);
        }
      });

      this.proc.on('error', (err) => {
        clearTimeout(timeout);
        reject(new Error(`Failed to start llmcode: ${err.message}`));
      });

      this.proc.on('exit', (code) => {
        this._ready = false;
        this._port = null;
        if (code !== 0 && code !== null) {
          vscode.window.showErrorMessage(`llmcode exited with code ${code}`);
        }
      });
    });
  }

  stop(): void {
    if (this.proc) {
      this.proc.kill('SIGTERM');
      this.proc = null;
      this._ready = false;
      this._port = null;
    }
  }

  dispose(): void {
    this.stop();
  }
}
