#!/usr/bin/env node
// Codex CLI hook router for Zeus.
// Reshapes Codex stdin into Claude-compatible payloads and forwards to
// .claude/hooks/dispatch.py. Path discovery is router-relative so it works
// from any clone path without per-operator setup.
//
// Exit codes: always 0 (advisory only; never blocks).

import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const ROUTER_DIR = path.dirname(fileURLToPath(import.meta.url));
const ZEUS_ROOT = path.resolve(ROUTER_DIR, '../..');
const DISPATCH = path.join(ZEUS_ROOT, '.claude/hooks/dispatch.py');
const hookId = process.argv[2] || '';

const EDIT_PATH_HOOKS = new Set([
  'pre_edit_architecture',
  'pre_write_capability_gate',
]);

function readStdin() {
  try {
    return readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

function parsePayload(raw) {
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function normalizePayload(input) {
  const payload = {...input};
  payload.hook_event_name = payload.hook_event_name || payload.hookEventName || '';
  payload.tool_name = payload.tool_name || payload.toolName || '';
  payload.tool_input = payload.tool_input || payload.toolInput || {};
  if (payload.tool_response === undefined && payload.toolResponse !== undefined) {
    payload.tool_response = payload.toolResponse;
  }
  return payload;
}

function isUnderZeus(cwd) {
  const candidate = path.resolve(cwd || process.cwd());
  const root = path.resolve(ZEUS_ROOT);
  const rel = path.relative(root, candidate);
  return rel === '' || (!!rel && !rel.startsWith('..') && !path.isAbsolute(rel));
}

function extractPatchPaths(patchText) {
  if (typeof patchText !== 'string') return [];
  const paths = [];
  const seen = new Set();
  const re = /^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$/gm;
  let match;
  while ((match = re.exec(patchText)) !== null) {
    const filePath = match[1].trim();
    if (filePath && !seen.has(filePath)) {
      seen.add(filePath);
      paths.push(filePath);
    }
  }
  return paths;
}

function payloadForPath(payload, filePath, allPaths) {
  return {
    ...payload,
    tool_input: {
      ...(payload.tool_input || {}),
      file_path: filePath,
      path: filePath,
      codex_patch_paths: allPaths,
      codex_original_tool_name: payload.tool_name,
    },
  };
}

function runDispatch(payload) {
  const result = spawnSync('python3', [DISPATCH, hookId], {
    cwd: ZEUS_ROOT,
    input: JSON.stringify(payload),
    encoding: 'utf8',
    timeout: 30000,
  });

  if (process.env.ZEUS_CODEX_ROUTER_DEBUG === '1' && result.stderr) {
    process.stderr.write(result.stderr);
  }

  if (result.error || result.status !== 0) {
    return '';
  }
  return (result.stdout || '').trim();
}

function contextFromDispatchStdout(stdout) {
  if (!stdout) return '';
  try {
    const parsed = JSON.parse(stdout);
    return parsed?.hookSpecificOutput?.additionalContext || '';
  } catch {
    return stdout;
  }
}

function codexAdjustedContext(context) {
  if (!context) return '';
  if (hookId !== 'pr_open_monitor_arm') return context;
  if (!context.includes('Monitor(')) return context;

  return [
    context,
    '',
    'CODEX NOTE: the advisory above mentions Claude Monitor. Codex does not have that tool.',
    'Use a Codex heartbeat/automation or manual polling instead. Reviewer appearance is a repair trigger, not completion.',
    'The monitor must notify on failing/pending checks, new non-self comments/reviews, and unresolved actionable reviewThreads.',
    'After notification: fetch thread-aware reviewThreads, fix code/tests, push one repair batch, and resolve threads only after evidence.',
    'Keep watching while the PR remains open; do not stop merely because checks pass or reviewers appeared.',
    'Minimum manual checks:',
    '  gh pr checks <pr-number> --json name,bucket',
    '  gh pr view <pr-number> --json reviewDecision,statusCheckRollup,mergeStateStatus,isDraft,latestReviews,state',
    '  gh api graphql ... reviewThreads { isResolved isOutdated comments { nodes { body author { login } } } }',
  ].join('\n');
}

function emitAdditionalContext(eventName, contexts) {
  const text = contexts.map(codexAdjustedContext).filter(Boolean).join('\n\n---\n\n');
  if (!text) return;
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: eventName || 'PreToolUse',
      additionalContext: text,
    },
  }));
}

function main() {
  if (!hookId) process.exit(0);

  const payload = normalizePayload(parsePayload(readStdin()));
  if (!isUnderZeus(payload.cwd)) process.exit(0);

  const eventName = payload.hook_event_name || 'PreToolUse';
  const contexts = [];

  if (EDIT_PATH_HOOKS.has(hookId) && payload.tool_name === 'apply_patch') {
    const paths = extractPatchPaths(payload.tool_input?.command);
    if (paths.length > 0) {
      for (const filePath of paths) {
        contexts.push(contextFromDispatchStdout(
          runDispatch(payloadForPath(payload, filePath, paths))
        ));
      }
      emitAdditionalContext(eventName, contexts);
      process.exit(0);
    }
  }

  contexts.push(contextFromDispatchStdout(runDispatch(payload)));
  emitAdditionalContext(eventName, contexts);
}

main();
