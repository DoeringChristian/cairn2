import type {APIRequestContext} from '@playwright/test';
export const reportPage='/p/neural-rendering/r/field-guide';
/** The showcase report written by examples/report-showcase/run.py. */
export async function fieldGuide(request:APIRequestContext) {
  const reports=await (await request.get('/v1/reports?project=neural-rendering')).json();
  return reports.find((report:any)=>report.name==='field-guide');
}
