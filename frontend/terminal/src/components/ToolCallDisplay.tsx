import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';

// Each completed tool call and its result are rendered as separate static
// history entries (see <Static> usage), so this component renders a single
// transcript item rather than a paired tool/result.
export function ToolCallDisplay({
	item,
	outputStyle,
}: {
	item: TranscriptItem;
	outputStyle?: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';

	if (item.role === 'tool') {
		const toolName = item.tool_name ?? 'tool';
		const summary = summarizeInput(toolName, item.tool_input, item.text).replace(/\s+/g, ' ').trim();

		if (isCodexStyle) {
			return (
				<Box marginLeft={0} flexDirection="column">
					<Text dimColor>{`• ${toolName}${summary ? ` ${summary}` : ''}`}</Text>
				</Box>
			);
		}

		return (
			<Box marginLeft={2} flexDirection="column">
				<Text>
					<Text color={theme.colors.accent} bold>{theme.icons.tool}</Text>
					<Text color={theme.colors.accent} bold>{toolName}</Text>
					<Text dimColor> {summary}</Text>
				</Text>
			</Box>
		);
	}

	if (item.role === 'tool_result') {
		const lines = item.text.split('\n').filter((line) => line.trim());

		if (item.is_error) {
			const maxErrLines = isCodexStyle ? 8 : 5;
			const display = lines.length > maxErrLines
				? [...lines.slice(0, maxErrLines), `... (${lines.length - maxErrLines} more lines)`]
				: lines;

			if (isCodexStyle) {
				return (
					<Box marginLeft={0} flexDirection="column">
						{display.map((line, i) => {
							const prefix = i === display.length - 1 ? '└ ' : '│ ';
							return (
								<Text key={i} color={theme.colors.error}>
									{prefix}
									{line}
								</Text>
							);
						})}
					</Box>
				);
			}

			return (
				<Box marginLeft={4} flexDirection="column">
					{display.map((line, i) => (
						<Text key={i} color={theme.colors.error}>{line}</Text>
					))}
				</Box>
			);
		}

		// Successful result — show a bounded preview so users can follow the
		// intermediate execution process without dumping the full output.
		if (isCodexStyle) {
			return <Text dimColor>{lines.length > 0 ? `${lines.length}L` : ''}</Text>;
		}

		const lineCount = lines.length;
		const resultLabel = lineCount > 0 ? `${lineCount}L` : theme.icons.success.trim();
		const preview = previewResult(item.text);

		return (
			<Box marginLeft={4} flexDirection="column">
				<Text dimColor> → {resultLabel}</Text>
				{preview?.map((line, i) => (
					<Text key={i} dimColor>{line}</Text>
				))}
			</Box>
		);
	}

	return <Text>{item.text}</Text>;
}

const RESULT_PREVIEW_MAX_LINES = 3;
const RESULT_PREVIEW_MAX_LINE_LENGTH = 200;

// Bounded preview of a successful tool result.
function previewResult(text: string): string[] | null {
	const lines = text.split('\n').map((line) => line.trimEnd()).filter((line) => line.trim());
	if (lines.length === 0) {
		return null;
	}
	return lines.slice(0, RESULT_PREVIEW_MAX_LINES).map((line) =>
		line.length > RESULT_PREVIEW_MAX_LINE_LENGTH
			? `${line.slice(0, RESULT_PREVIEW_MAX_LINE_LENGTH)}…`
			: line,
	);
}

function summarizeInput(toolName: string, toolInput?: Record<string, unknown>, fallback?: string): string {
	if (!toolInput) {
		return fallback?.slice(0, 80) ?? '';
	}
	const lower = toolName.toLowerCase();
	if (lower === 'bash' && toolInput.command) {
		return String(toolInput.command).slice(0, 120);
	}
	if ((lower === 'read' || lower === 'fileread') && toolInput.file_path) {
		return String(toolInput.file_path);
	}
	if ((lower === 'write' || lower === 'filewrite') && toolInput.file_path) {
		return String(toolInput.file_path);
	}
	if ((lower === 'edit' || lower === 'fileedit') && toolInput.file_path) {
		return String(toolInput.file_path);
	}
	if (lower === 'grep' && toolInput.pattern) {
		return `/${String(toolInput.pattern)}/`;
	}
	if (lower === 'glob' && toolInput.pattern) {
		return String(toolInput.pattern);
	}
	if (lower === 'agent' && toolInput.description) {
		return String(toolInput.description);
	}
	const entries = Object.entries(toolInput);
	if (entries.length > 0) {
		const [key, val] = entries[0];
		return `${key}=${String(val).slice(0, 60)}`;
	}
	return fallback?.slice(0, 80) ?? '';
}
