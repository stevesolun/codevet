import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

interface CodevetOutput {
  file_path: string;
  original_code: string;
  fixed_code: string | null;
  confidence: {
    score: number;
    grade: string;
    explanation: string;
    pass_rate: number;
    critique_score: number;
  };
  vet_result: {
    total: number;
    passed: number;
    failed: number;
    errors: number;
  };
  fix_result: {
    success: boolean;
    iterations_used: number;
  } | null;
  model_used: string;
  duration_seconds: number;
}

export function activate(context: vscode.ExtensionContext): void {
  const disposable = vscode.commands.registerCommand(
    "codevet.fixFile",
    async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showErrorMessage("No active editor.");
        return;
      }

      const filePath = editor.document.uri.fsPath;
      if (!filePath.endsWith(".py")) {
        vscode.window.showWarningMessage(
          "CodeVet currently supports Python files only."
        );
        return;
      }

      const panel = vscode.window.createWebviewPanel(
        "codevetResults",
        `CodeVet: ${filePath.split(/[/\\]/).pop()}`,
        vscode.ViewColumn.Beside,
        { enableScripts: true }
      );

      panel.webview.html = getLoadingHtml();

      try {
        const { stdout } = await execFileAsync("codevet", [
          "fix",
          filePath,
          "--json",
        ]);
        const result: CodevetOutput = JSON.parse(stdout);
        panel.webview.html = getResultHtml(result);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        panel.webview.html = getErrorHtml(message);
      }
    }
  );

  context.subscriptions.push(disposable);
}

export function deactivate(): void {}

function getLoadingHtml(): string {
  return `<!DOCTYPE html>
<html><body style="font-family:system-ui;padding:20px;background:#1e1e1e;color:#d4d4d4">
<h2>CodeVet</h2>
<p>Running analysis in Docker sandbox...</p>
<div style="display:inline-block;width:20px;height:20px;border:3px solid #555;
border-top:3px solid #0af;border-radius:50%;animation:spin 1s linear infinite"></div>
<style>@keyframes spin{to{transform:rotate(360deg)}}</style>
</body></html>`;
}

function getResultHtml(result: CodevetOutput): string {
  const gradeColor: Record<string, string> = {
    A: "#4caf50",
    B: "#2196f3",
    C: "#ff9800",
    D: "#f44336",
    F: "#9c27b0",
  };
  const color = gradeColor[result.confidence.grade] || "#888";

  const diffHtml = result.fixed_code
    ? `<h3>Fixed Code</h3><pre style="background:#1a1a2e;padding:12px;border-radius:6px;
overflow-x:auto;font-size:13px">${escapeHtml(result.fixed_code)}</pre>`
    : "<p>No fixes needed — all tests passed.</p>";

  return `<!DOCTYPE html>
<html><body style="font-family:system-ui;padding:20px;background:#1e1e1e;color:#d4d4d4">
<h2>CodeVet Results</h2>

<div style="display:flex;gap:16px;margin:16px 0">
  <div style="background:${color};color:#fff;padding:12px 24px;border-radius:8px;
  font-size:28px;font-weight:bold;text-align:center;min-width:80px">
    ${result.confidence.grade}<br>
    <span style="font-size:14px">${result.confidence.score}/100</span>
  </div>
  <div style="flex:1">
    <p><strong>File:</strong> ${escapeHtml(result.file_path)}</p>
    <p><strong>Model:</strong> ${escapeHtml(result.model_used)}</p>
    <p><strong>Duration:</strong> ${result.duration_seconds}s</p>
    <p><strong>Tests:</strong> ${result.vet_result.passed} passed,
    ${result.vet_result.failed} failed, ${result.vet_result.errors} errors
    (${result.vet_result.total} total)</p>
    ${result.fix_result ? `<p><strong>Fix:</strong> ${result.fix_result.success ? "Success" : "Failed"} in ${result.fix_result.iterations_used} iteration(s)</p>` : ""}
  </div>
</div>

<h3>Confidence</h3>
<p>${escapeHtml(result.confidence.explanation)}</p>

${diffHtml}

<p style="color:#666;font-size:12px;margin-top:24px">
  Powered by codevet v0.1.0 — local AI code vetting
</p>
</body></html>`;
}

function getErrorHtml(message: string): string {
  return `<!DOCTYPE html>
<html><body style="font-family:system-ui;padding:20px;background:#1e1e1e;color:#d4d4d4">
<h2 style="color:#f44336">CodeVet Error</h2>
<pre style="background:#2d1111;padding:12px;border-radius:6px;color:#ff6b6b">
${escapeHtml(message)}</pre>
<p>Make sure Docker and Ollama are running.</p>
</body></html>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
