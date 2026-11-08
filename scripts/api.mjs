// Starts the FastAPI backend with the project's virtualenv Python.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const python = process.platform === "win32"
  ? join(backend, ".venv", "Scripts", "python.exe")
  : join(backend, ".venv", "bin", "python");

if (!existsSync(python)) {
  console.error("Backend virtualenv not found. Run `npm run setup` first.");
  process.exit(1);
}

const args = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"];
if (process.argv.includes("--reload")) args.push("--reload", "--reload-dir", "app");
else console.log("HustleAI running at http://127.0.0.1:8000");

const child = spawn(python, args, { cwd: backend, stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
