#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const skillRoot = path.resolve(path.dirname(scriptPath), '..');
const args = process.argv.slice(2);
const targetArg = readOption('--target');
const targetRoot = path.resolve(
  targetArg || process.env.IMPECCABLE_PROJECT_SKILL_DIR || path.join(process.cwd(), '.agents', 'skills', 'impeccable'),
);

function readOption(name) {
  const index = args.indexOf(name);
  if (index === -1) return null;
  const value = args[index + 1];
  if (!value || value.startsWith('--')) {
    throw new Error(`${name} requires a value`);
  }
  return value;
}

function hasSkillMd(dir) {
  return fs.existsSync(path.join(dir, 'SKILL.md'));
}

function isEmptyDir(dir) {
  try {
    return fs.readdirSync(dir).length === 0;
  } catch {
    return false;
  }
}

if (!hasSkillMd(skillRoot)) {
  throw new Error(`Impeccable source skill is incomplete: ${skillRoot}`);
}

if (path.resolve(skillRoot) === targetRoot) {
  console.log(`Impeccable skill already available at ${targetRoot}`);
  process.exit(0);
}

if (hasSkillMd(targetRoot)) {
  console.log(`Impeccable skill already available at ${targetRoot}`);
  process.exit(0);
}

if (fs.existsSync(targetRoot) && !isEmptyDir(targetRoot)) {
  throw new Error(`Target exists but is not an Impeccable skill: ${targetRoot}`);
}

fs.mkdirSync(path.dirname(targetRoot), { recursive: true });
fs.cpSync(skillRoot, targetRoot, {
  recursive: true,
  filter(source) {
    const name = path.basename(source);
    if (name === '.git' || name === 'node_modules' || name === '__pycache__') return false;
    if (name.endsWith('.pyc')) return false;
    return true;
  },
});

console.log(`Installed Impeccable skill files to ${targetRoot}`);
