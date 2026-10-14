import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {ToolCallDisplay} from './ToolCallDisplay.js';

const stripAnsi = (value: string): string => value.replace(/\u001B\[[0-9;?]*[ -/]*[@-~]/g, '');
const nextLoopTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

type InkTestStdout = PassThrough & {
	isTTY: boolean;
	columns: number;
	rows: number;
	cursorTo: () => boolean;
	clearLine: () => boolean;
	moveCursor: () => boolean;
};

function createTestStdout(): InkTestStdout {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		columns: 120,
		rows: 40,
		cursorTo: () => true,
		clearLine: () => true,
		moveCursor: () => true,
	});
}

async function waitForOutputToStabilize(getOutput: () => string): Promise<string> {
	let previous = '';
	let sawOutput = false;

	for (let i = 0; i < 50; i++) {
		await nextLoopTurn();
		const current = getOutput();
		sawOutput ||= current.length > 0;
		if (sawOutput && current === previous) {
			return current;
		}

		previous = current;
	}

	throw new Error(`Ink output did not stabilize: ${JSON.stringify(previous)}`);
}

async function renderToolCall(
	item: TranscriptItem,
	resultItem?: TranscriptItem,
	outputStyle?: string,
): Promise<string> {
	const stdout = createTestStdout();

	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ThemeProvider initialTheme="default">
			<ToolCallDisplay item={item} resultItem={resultItem} outputStyle={outputStyle} />
		</ThemeProvider>,
		{stdout: stdout as unknown as NodeJS.WriteStream, debug: true, patchConsole: false},
	);

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	await waitForOutputToStabilize(() => output);
	instance.cleanup();

	return stripAnsi(stableOutput);
}

test('shows a running indicator for an in-flight tool without a result', async () => {
	const item: TranscriptItem = {
		role: 'tool',
		text: 'bash {"command":"ls -la"}',
		tool_name: 'bash',
		tool_input: {command: 'ls -la'},
	};

	const output = await renderToolCall(item);

	assert.match(output, /bash/);
	assert.match(output, /ls -la/);
	assert.match(output, /… running/);
});

test('shows a bounded preview of a successful tool result', async () => {
	const item: TranscriptItem = {
		role: 'tool',
		text: 'read {"file_path":"/tmp/a.txt"}',
		tool_name: 'read',
		tool_input: {file_path: '/tmp/a.txt'},
	};
	const resultItem: TranscriptItem = {
		role: 'tool_result',
		text: 'first line\nsecond line\nthird line',
		tool_name: 'read',
		is_error: false,
	};

	const output = await renderToolCall(item, resultItem);

	assert.match(output, /→ 3L/);
	assert.match(output, /first line/);
	assert.match(output, /second line/);
	assert.match(output, /third line/);
});

test('truncates a successful result preview to three lines and reports the total', async () => {
	const item: TranscriptItem = {
		role: 'tool',
		text: 'read {"file_path":"/tmp/a.txt"}',
		tool_name: 'read',
		tool_input: {file_path: '/tmp/a.txt'},
	};
	const resultItem: TranscriptItem = {
		role: 'tool_result',
		text: 'one\ntwo\nthree\nfour\nfive',
		tool_name: 'read',
		is_error: false,
	};

	const output = await renderToolCall(item, resultItem);

	assert.match(output, /→ 5L/);
	assert.match(output, /one/);
	assert.match(output, /two/);
	assert.match(output, /three/);
	assert.doesNotMatch(output, /four/);
	assert.doesNotMatch(output, /five/);
});

test('truncates overly long result preview lines', async () => {
	const longLine = 'x'.repeat(500);
	const item: TranscriptItem = {
		role: 'tool',
		text: 'bash {"command":"cat big.txt"}',
		tool_name: 'bash',
		tool_input: {command: 'cat big.txt'},
	};
	const resultItem: TranscriptItem = {
		role: 'tool_result',
		text: longLine,
		tool_name: 'bash',
		is_error: false,
	};

	const output = await renderToolCall(item, resultItem);

	// A 500-char line must be cut to the preview cap plus an ellipsis.
	assert.ok(output.includes('…'), `Expected a truncated ellipsis in output: ${JSON.stringify(output)}`);
	assert.ok(output.length < longLine.length + 200, 'Preview output should be far shorter than the full result line');
});

test('still renders error results as error lines', async () => {
	const item: TranscriptItem = {
		role: 'tool',
		text: 'bash {"command":"false"}',
		tool_name: 'bash',
		tool_input: {command: 'false'},
	};
	const resultItem: TranscriptItem = {
		role: 'tool_result',
		text: 'command not found',
		tool_name: 'bash',
		is_error: true,
	};

	const output = await renderToolCall(item, resultItem);

	assert.match(output, /command not found/);
});
