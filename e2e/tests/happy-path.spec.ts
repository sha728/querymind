import { expect, test } from '@playwright/test'

// The happy path through the real stack (design §13.4, N2): browser -> nginx -> .NET API ->
// engine -> read-only Northwind, with only the LLM replaced by the deterministic stub.
const QUESTION = 'Which 5 customers spent the most last month?'

test('register, log in, ask, see table + chart + SQL, find it in history', async ({ page }) => {
  const email = `e2e-${String(Date.now())}@example.com`
  const password = 'correct horse battery'

  // Register
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByText('Account created. Sign in to continue.')).toBeVisible()

  // Log in (the email is pre-filled)
  await expect(page.getByLabel('Email')).toHaveValue(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Ask about your data' })).toBeVisible()
  expect(await page.evaluate(() => sessionStorage.getItem('qm.token'))).toBeTruthy()

  // Ask
  await page.getByLabel('Question', { exact: true }).fill(QUESTION)
  await page.getByRole('button', { name: 'Ask' }).click()
  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer).toBeVisible()

  // Summary (the stub's second call) and SQL
  await expect(answer.getByText('Ernst Handel spent the most last month')).toBeVisible()
  const sql = answer.getByRole('region', { name: 'SQL' })
  await expect(sql).toContainText('ORDER BY total_spent DESC')
  await expect(sql).toContainText('LIMIT 5')

  // Table: 5 rows of the real, seeded data
  const table = answer.getByRole('region', { name: 'Result' }).getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['company_name', 'total_spent'])
  await expect(table.locator('tbody tr')).toHaveCount(5)
  await expect(table.locator('tbody tr').first()).toContainText('Ernst Handel')

  // Chart: one category + one non-negative measure in 5 rows -> the engine recommends a pie
  // (design §9 rule C3), with bar and table allowed. Drawn by Recharts.
  const chart = answer.getByRole('img', { name: /chart of total_spent by company_name/ })
  await expect(chart).toHaveAttribute('data-chart-type', 'pie')
  await expect(chart.locator('.recharts-pie-sector')).toHaveCount(5)
  await expect(chart.locator('.recharts-legend-item-text')).toHaveText([
    'Ernst Handel', 'Save-a-lot Markets', 'Queen Cozinha', 'Lehmanns Marktstand', 'Rattlesnake Canyon Grocery',
  ]) // data order, matching the slice colours
  await expect(answer.getByRole('group', { name: 'Chart type' }).getByRole('button')).toHaveText(['Pie', 'Bar', 'Table'])
  await page.screenshot({ path: 'test-results/ask-pie.png', fullPage: true })

  // Switch to bar
  await answer.getByRole('button', { name: 'Bar' }).click()
  await expect(chart).toHaveAttribute('data-chart-type', 'bar')
  await expect(chart.locator('.recharts-bar-rectangle')).toHaveCount(5)
  await page.screenshot({ path: 'test-results/ask-bar.png', fullPage: true })

  // History
  await page.getByRole('link', { name: 'History' }).click()
  const history = page.getByRole('table', { name: 'Question history' })
  await expect(history.locator('tbody tr')).toHaveCount(1)
  await expect(history.locator('tbody tr').first()).toContainText(QUESTION)
  await expect(history.locator('tbody tr').first()).toContainText('Answered')
})
