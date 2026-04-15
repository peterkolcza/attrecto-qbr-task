const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  // 1. Landing page
  console.log('1. Loading landing page...');
  await page.goto('http://127.0.0.1:8888/', { waitUntil: 'networkidle' });
  await page.screenshot({ path: '/tmp/qbr_1_landing.png', fullPage: true });
  console.log('   Screenshot saved');

  // 2. Start analysis via API
  console.log('\n2. Starting demo analysis...');
  const analyzeResult = await page.evaluate(async () => {
    const resp = await fetch('/analyze', { method: 'POST' });
    return resp.json();
  });
  const jobId = analyzeResult.job_id;
  console.log(`   Job: ${jobId}`);

  // 3. Navigate to job page
  await page.goto(`http://127.0.0.1:8888/jobs/${jobId}`, { waitUntil: 'domcontentloaded', timeout: 10000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/tmp/qbr_2_job_processing.png', fullPage: true });
  console.log('   Processing screenshot saved');

  // 4. Wait for completion (poll the job status badge)
  console.log('\n4. Waiting for analysis...');
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(5000);
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 10000 });
    await page.waitForTimeout(1000);

    // Check the status badge
    const badge = await page.$('.bg-green-100');
    const errorBadge = await page.$('.bg-red-100');

    if (badge) {
      console.log(`   Complete after ${(i+1)*5}s!`);
      await page.screenshot({ path: '/tmp/qbr_3_job_done.png', fullPage: true });

      // Click report link
      const reportLink = await page.$('a:has-text("View Full Report")');
      if (reportLink) {
        await reportLink.click();
        await page.waitForLoadState('domcontentloaded');
        await page.screenshot({ path: '/tmp/qbr_4_report.png', fullPage: true });
        console.log('   Report screenshot saved');
      }
      break;
    }
    if (errorBadge) {
      console.log(`   ERROR after ${(i+1)*5}s`);
      await page.screenshot({ path: '/tmp/qbr_3_job_error.png', fullPage: true });
      break;
    }
    if (i % 6 === 5) console.log(`   Still processing... ${(i+1)*5}s`);
  }

  await browser.close();
  console.log('\nDone.');
})();
