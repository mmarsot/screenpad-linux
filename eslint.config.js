// Lint for the GNOME Shell extension (GJS, ES modules). Formatting is
// Prettier's job (.prettierrc.json); this checks for mistakes only.
import js from '@eslint/js';
import globals from 'globals';

export default [
    {
        files: ['gnome-shell/**/*.js'],
        ...js.configs.recommended,
        languageOptions: {
            ecmaVersion: 2024,
            sourceType: 'module',
            // What GJS and GNOME Shell put in scope.
            globals: {
                ...globals.es2024,
                global: 'readonly',
                log: 'readonly',
                logError: 'readonly',
                print: 'readonly',
                printerr: 'readonly',
                console: 'readonly',
                TextEncoder: 'readonly',
                TextDecoder: 'readonly',
                setTimeout: 'readonly',
                clearTimeout: 'readonly',
                setInterval: 'readonly',
                clearInterval: 'readonly',
            },
        },
        rules: {
            ...js.configs.recommended.rules,
            'no-unused-vars': ['error', {argsIgnorePattern: '^_', ignoreRestSiblings: true}],
            eqeqeq: ['error', 'smart'],
            'prefer-const': 'error',
            'no-var': 'error',
        },
    },
    {
        files: ['eslint.config.js'],
        languageOptions: {sourceType: 'module'},
    },
];
