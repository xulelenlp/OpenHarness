import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

// Completed transcript items are rendered through Ink's <Static> (written once
// and never re-rendered), which keeps the live layout short so Ink never has to
// clear and fully redraw the screen. The streaming text is the only in-progress
// content, and it is tail-capped below so it cannot push the live layout past
// the terminal height.
const MAX_STREAM_LINES = 10;

export function StaticTranscriptItem({
	item,
	outputStyle,
}: {
	item: TranscriptItem;
	outputStyle: string;
}): React.JSX.Element {
	const {theme} = useTheme();

	if (item.role === 'tool' || item.role === 'tool_result') {
		return <ToolCallDisplay item={item} outputStyle={outputStyle} />;
	}

	return <MessageRow item={item} theme={theme} outputStyle={outputStyle} />;
}

export function StreamingView({
	assistantBuffer,
	toolProgressBuffer,
	showWelcome,
	outputStyle,
	productName,
	slogan,
}: {
	assistantBuffer: string;
	toolProgressBuffer: string;
	showWelcome: boolean;
	outputStyle: string;
	productName?: string;
	slogan?: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';

	// Live progress emitted by a running tool (e.g. an external agent's streamed
	// report). Rendered as plain, tail-capped text so the live layout never grows
	// past the terminal height — this is the "intermediate execution process"
	// that used to be invisible behind a static spinner.
	if (toolProgressBuffer) {
		const {lines, truncated} = tailLines(toolProgressBuffer, MAX_STREAM_LINES);
		return (
			<Box marginTop={0} flexDirection="column">
				<Box marginLeft={4} flexDirection="column">
					<Text dimColor>{theme.icons.tool.trim()} streaming…</Text>
					<Text>{lines}</Text>
					{truncated ? <Text dimColor>… (older output scrolled)</Text> : null}
				</Box>
			</Box>
		);
	}

	if (!assistantBuffer) {
		return showWelcome ? (
			<WelcomeBanner productName={productName} slogan={slogan} />
		) : (
			<></>
		);
	}

	const {lines, truncated} = tailLines(assistantBuffer, MAX_STREAM_LINES);

	if (isCodexStyle) {
		return (
			<Box flexDirection="column">
				<Text>{lines}</Text>
				{truncated ? <Text dimColor>… streaming</Text> : null}
			</Box>
		);
	}

	return (
		<Box marginTop={1} marginBottom={0} flexDirection="column">
			<Text>
				<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
			</Text>
			<Box marginLeft={2} flexDirection="column">
				{/* Streaming text is rendered as plain text (no markdown re-lex per
				    flush) and tail-capped so the live layout never grows past the
				    terminal height. Full markdown formatting is applied once the
				    assistant message completes and enters <Static>. */}
				<Text>{lines}</Text>
				{truncated ? <Text dimColor>… streaming</Text> : null}
			</Box>
		</Box>
	);
}

function tailLines(text: string, maxLines: number): {lines: string; truncated: boolean} {
	const lines = text.split('\n');
	if (lines.length <= maxLines) {
		return {lines: text, truncated: false};
	}
	return {lines: lines.slice(-maxLines).join('\n'), truncated: true};
}

function MessageRow({
	item,
	theme,
	outputStyle,
}: {
	item: TranscriptItem;
	theme: ReturnType<typeof useTheme>['theme'];
	outputStyle: string;
}): React.JSX.Element {
	const isCodexStyle = outputStyle === 'codex';

	switch (item.role) {
		case 'user':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text dimColor>{'> '}</Text>
							<Text>{item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0}>
					<Text>
						<Text color={theme.colors.secondary} bold>{theme.icons.user}</Text>
						<Text>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'assistant':
			if (isCodexStyle) {
				return (
					<Box marginTop={0} marginBottom={0}>
						<Text>{item.text}</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0} flexDirection="column">
					<Text>
						<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
					</Text>
					<Box marginLeft={2} flexDirection="column">
						<MarkdownText content={item.text} />
					</Box>
				</Box>
			);

		case 'system':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text color={theme.colors.warning}>[system]</Text>
							<Text> {item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={0}>
					<Text>
						<Text color={theme.colors.warning}>{theme.icons.system}</Text>
						<Text color={theme.colors.warning}>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'status':
			return (
				<Box marginTop={0}>
					<Text color={theme.colors.info}>{item.text}</Text>
				</Box>
			);

		case 'log':
			return (
				<Box>
					<Text dimColor>{item.text}</Text>
				</Box>
			);

		default:
			return (
				<Box>
					<Text>{item.text}</Text>
				</Box>
			);
	}
}
