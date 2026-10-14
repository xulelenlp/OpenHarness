import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import {StreamingView} from './ConversationView.js';

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

async function renderStreaming(assistantBuffer: string, showWelcome = false): Promise<string> {
	const stdout = createTestStdout();

	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ThemeProvider initialTheme="default">
			<StreamingView
				assistantBuffer={assistantBuffer}
				showWelcome={showWelcome}
				outputStyle="default"
				productName="test"
				slogan="slogan"
			/>
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

test('renders the full streaming buffer when it is within the line cap', async () => {
	const output = await renderStreaming('line one\nline two\nline three');

	assert.match(output, /line one/);
	assert.match(output, /line two/);
	assert.match(output, /line three/);
});

test('tail-caps a long streaming buffer to the last lines', async () => {
	const lines = Array.from({length: 20}, (_, i) => `stream line ${i}`);
	const output = await renderStreaming(lines.join('\n'));

	assert.match(output, /stream line 19/);
	assert.match(output, /stream line 10/);
	assert.match(output, /… streaming/);
	assert.doesNotMatch(output, /stream line 0/);
	assert.doesNotMatch(output, /stream line 9/);
});

test('renders the welcome banner when idle', async () => {
	const output = await renderStreaming('', true);

	assert.match(output, /test/);
});
