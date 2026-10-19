import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { ApplicationError, commandMain, invokeCLI, privateDirectory } from './adapter-runtime.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const cases = new Set(["starter-01", "starter-02", "starter-03", "busy-01", "busy-02", "busy-03", "ambiguous-01", "ambiguous-02", "ambiguous-03", "boundary-01", "boundary-02", "boundary-03"]);
export async function runApplication(input, parameters, signal, invocation) {
  if (Object.keys(input).some(key => !["caseId", "action", "task", "allowWrites", "intent", "recordId", "models"].includes(key)) || !cases.has(input.caseId) || !['run', 'inspect'].includes(input.action) ||
      typeof input.task !== 'string' || !input.task.trim() || input.task.length > 8000 || input.task.includes('\0') || typeof input.allowWrites !== 'boolean' ||
      (input.allowWrites ? !input.intent || typeof input.intent !== 'object' || Array.isArray(input.intent) || JSON.stringify(input.intent).length > 8000 : input.intent !== null) ||
      (input.action === 'inspect' && input.allowWrites) || typeof input.recordId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(input.recordId) || parameters.model !== undefined || !input.models || typeof input.models !== 'object' || Array.isArray(input.models) || Object.keys(input.models).sort().join(',') !== 'extraction,risk_anthropic,risk_openai,summary' || !Object.values(input.models).every(value => typeof value === 'string' && /^[A-Za-z0-9._/-]{1,200}$/.test(value))) throw new Error('Supply reviewed explicit task identity/model settings and independent write grant with exact caller intent, or null for read-only.');
  const directory = await privateDirectory(root, invocation), workspace = join(directory, 'workspace');
  const cli = ['uv', 'run', '--locked', '--no-sync'];
  try {
    const initialized = await invokeCLI([...cli, 'python', 'firedrill/case-driver.py', 'init', workspace, input.caseId], { cwd: root, signal });
    const db = initialized.database;
    if (typeof db !== 'string' || !db.startsWith(workspace + '/')) throw new Error('Initialized database is outside the owned workspace.');
    const argv = input.action === 'inspect' ? [...cli, 'python', 'firedrill/case-driver.py', 'inspect', workspace] : [...cli, 'contract-review', '--db', db, '--document', input.recordId, '--request', input.task, '--output', join(directory, 'receipts'), '--extract-model', input.models.extraction, '--risk-anthropic-model', input.models.risk_anthropic, '--risk-openai-model', input.models.risk_openai, '--summary-model', input.models.summary];
    if (input.action === 'run') {
      if (input.allowWrites) argv.push('--allow-writes', '--intent-json', JSON.stringify(input.intent));
      if (parameters.model) argv.push('--model', parameters.model);
    }
    return { executionKind: input.action === 'inspect' ? 'application_state_inspection' : 'native_owned_application',
      application: await invokeCLI(argv, { cwd: root, signal }), localState: { directory, workspaceRoot: workspace, databaseFile: db } };
  } catch (error) { if (error instanceof ApplicationError) error.evidence.runDirectory = directory; throw error; }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await commandMain(runApplication);
