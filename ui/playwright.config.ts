import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:process.env.CAIRN_UI_URL || 'http://localhost:18765'},reporter:'list'});
