// One-time setup: Python venv + backend packages + frontend packages.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const win = process.platform === "win32";
const venvPython = join(backend, ".venv", win ? "Scripts/python.exe" : "bin/python");
const run = (cmd, args, cwd = root) => execFileSync(cmd, args, { cwd, stdio: "inherit", shell: win && cmd === "npm" });

if (!existsSync(venvPython)) {
  // Python 3.10-3.12 is recommended; the CUDA/ML wheels lag behind newer versions.
  const launcher = win ? "py" : "python3";
  const launcherArgs = win ? ["-3.12", "-m", "venv", ".venv"] : ["-m", "venv", ".venv"];
  console.log("Creating Python virtualenv…");
  run(launcher, launcherArgs, backend);
}
console.log("Installing backend packages (CUDA libraries are ~1.5 GB)…");
run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"], backend);
run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], backend);
console.log("Installing frontend packages…");
run("npm", ["install"]);
run("npm", ["install"], join(root, "frontend"));
console.log("\nDone. Start the app with:  npm run dev   (then open http://127.0.0.1:5173)");
