import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');
const sourceDir = path.join(repoRoot, 'dist');
const targetDir = path.join(process.cwd(), 'resources', 'runtime');
const wakewordSourceDir = path.join(repoRoot, 'models');
const wakewordTargetDir = path.join(targetDir, 'wakeword');

fs.mkdirSync(targetDir, { recursive: true });
fs.mkdirSync(wakewordTargetDir, { recursive: true });

const requiredRuntimeBinaries = ['pixelpilot-runtime.exe', 'orchestrator.exe', 'agent.exe'];
for (const file of requiredRuntimeBinaries) {
  const source = path.join(sourceDir, file);
  if (!fs.existsSync(source)) {
    throw new Error(`[x] Missing required runtime binary: ${source}`);
  }
  fs.copyFileSync(source, path.join(targetDir, file));
}

const wakewordAssets = [
  'pixie.onnx',
  'pixie.onnx.data',
  'melspectrogram.onnx',
  'embedding_model.onnx'
];
for (const file of wakewordAssets) {
  const source = path.join(wakewordSourceDir, file);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, path.join(wakewordTargetDir, file));
}
