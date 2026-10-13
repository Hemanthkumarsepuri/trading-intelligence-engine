import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    // Tests pin their own clock; nothing may depend on the real date.
    restoreMocks: true,
  },
});
