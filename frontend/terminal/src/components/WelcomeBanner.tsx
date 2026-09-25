import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';

const DEFAULT_PRODUCT_NAME = 'ABC Tech';
const DEFAULT_SLOGAN = 'An AI-powered coding assistant';

export function WelcomeBanner({
	productName,
	slogan,
}: {
	productName?: string;
	slogan?: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const name = (productName ?? '').trim() || DEFAULT_PRODUCT_NAME;
	const tagline = (slogan ?? '').trim() || DEFAULT_SLOGAN;

	return (
		<Box flexDirection="column" marginBottom={1}>
			<Box flexDirection="column" paddingX={0}>
				<Text color={theme.colors.primary} bold>{name}</Text>
				<Text> </Text>
				<Text dimColor> {tagline}</Text>
				<Text> </Text>
				<Text>
					<Text dimColor> </Text>
					<Text color={theme.colors.primary}>/help</Text>
					<Text dimColor> commands</Text>
					<Text dimColor>{'  '}|{'  '}</Text>
					<Text color={theme.colors.primary}>/model</Text>
					<Text dimColor> switch</Text>
					<Text dimColor>{'  '}|{'  '}</Text>
					<Text color={theme.colors.primary}>Ctrl+C</Text>
					<Text dimColor> exit</Text>
				</Text>
			</Box>
		</Box>
	);
}
