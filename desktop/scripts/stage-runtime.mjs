import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const sourceDir = path.join(repoRoot, 'dist');
const targetDir = path.join(process.cwd(), 'resources', 'runtime');
const wakewordSourceDir = path.join(repoRoot, 'model');
const wakewordTargetDir = path.join(targetDir, 'wakeword');

fs.mkdirSync(targetDir, { recursive: true });
fs.mkdirSync(wakewordTargetDir, { recursive: true });

const candidates = ['pixelpilot-runtime.exe', 'orchestrator.exe', 'agent.exe'];
for (const file of candidates) {
  const source = path.join(sourceDir, file);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, path.join(targetDir, file));
}

const wakewordAssets = ['pixie_model.pth', 'melspectrogram.onnx', 'embedding_model.onnx'];
for (const file of wakewordAssets) {
  const source = path.join(wakewordSourceDir, file);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, path.join(wakewordTargetDir, file));
}
