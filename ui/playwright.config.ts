import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./tests',
  use:{baseURL:process.env.CAIRN_UI_URL || 'http://localhost:18765'},
  reporter:'list',
  projects:[
    {name:'desktop', testIgnore:'**/mobile.spec.ts'},
    {name:'mobile-chromium', testMatch:'**/mobile.spec.ts', use:{browserName:'chromium',isMobile:true,hasTouch:true}},
    {name:'mobile-webkit', testMatch:'**/mobile.spec.ts', use:{browserName:'webkit',isMobile:true,hasTouch:true}},
  ],
});
