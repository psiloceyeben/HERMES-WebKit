#!/usr/bin/env node
/**
 * HERMES WEBKIT — cross-platform installer
 * Detects OS and runs the appropriate install script.
 *
 * Usage: npx hermes-webkit
 */

const { execSync, spawn } = require("child_process");
const os = require("os");
const https = require("https");
const fs = require("fs");
const path = require("path");

const REPO = "https://raw.githubusercontent.com/psiloceyeben/HERMES-WebKit/main";

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    https.get(url, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        download(res.headers.location, dest).then(resolve).catch(reject);
        return;
      }
      res.pipe(file);
      file.on("finish", () => { file.close(); resolve(); });
    }).on("error", (e) => { fs.unlinkSync(dest); reject(e); });
  });
}

async function main() {
  const platform = os.platform();
  const tmp = os.tmpdir();

  console.log("");
  console.log("  HERMES WEBKIT");
  console.log("  a vessel architecture for AI-inhabited websites");
  console.log("");

  if (platform === "win32") {
    const script = path.join(tmp, "hermes-install.ps1");
    console.log("  Downloading installer...");
    await download(`${REPO}/install.ps1`, script);
    console.log("  Launching PowerShell installer...\n");
    const ps = spawn("powershell", ["-ExecutionPolicy", "Bypass", "-File", script], {
      stdio: "inherit"
    });
    ps.on("close", (code) => process.exit(code));
  } else {
    const script = path.join(tmp, "hermes-run");
    console.log("  Downloading installer...");
    await download(`${REPO}/run`, script);
    fs.chmodSync(script, "755");
    console.log("  Launching installer...\n");
    const sh = spawn("sudo", ["bash", script], {
      stdio: "inherit"
    });
    sh.on("close", (code) => process.exit(code));
  }
}

main().catch((e) => {
  console.error("  Error:", e.message);
  process.exit(1);
});
