import { useCallback, useEffect, useState } from 'react'
import { HISTORY_PAGE_SIZE, listHistory, rerunHistory } from '../api/history'
import type { AskResponse, HistoryItem, HistoryPage as Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { AnswerView } from '../components/AnswerView'
import { ErrorNotice } from '../components/ErrorNotice'
import { toRequestError, type RequestError } from '../components/requestError'

const STATUS_LABELS: Record<HistoryItem['status'], string> = {
  success: 'Answered',
  failed: 'Failed',
  blocked: 'Blocked',
  cannot_answer: "Can't answer",
  error: 'Service error',
}

const dateFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

/** Past questions with re-run (R7.1, R7.2); admins can switch to every user's history (R8.2). */
export function HistoryPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin' // a UI hint only: the API checks the role itself
  const [allUsers, setAllUsers] = useState(false)
  const [pageNo, setPageNo] = useState(1)
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<RequestError | null>(null)
  const [rerunning, setRerunning] = useState<string | null>(null)
  const [rerunResult, setRerunResult] = useState<AskResponse | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    listHistory(pageNo, allUsers, controller.signal)
      .then((p) => { setPage(p); setError(null) })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(toRequestError(e, 'Could not load the history.'))
      })
    return () => { controller.abort() }
  }, [pageNo, allUsers, reload])

  const rerun = useCallback(async (item: HistoryItem) => {
    setRerunning(item.id)
    setRerunResult(null)
    setError(null)
    try {
      setRerunResult(await rerunHistory(item.id))
      setPageNo(1)
      setReload((n) => n + 1) // the re-run is a new entry at the top
    } catch (e) {
      setError(toRequestError(e, 'Could not re-run the question.'))
    } finally {
      setRerunning(null)
    }
  }, [])

  const pages = page === null ? 1 : Math.max(1, Math.ceil(page.total / HISTORY_PAGE_SIZE))

  return (
    <>
      <h1>History</h1>
      {isAdmin && (
        <label className="toggle">
          <input type="checkbox" checked={allUsers} onChange={(e) => { setAllUsers(e.target.checked); setPageNo(1) }} />
          All users
        </label>
      )}
      {error !== null && <ErrorNotice error={error} />}

      {rerunResult !== null && (
        <section aria-label="Re-run result" className="rerun-result">
          <h2>Re-run: {rerunResult.question}</h2>
          <AnswerView key={rerunResult.historyId} result={rerunResult} />
        </section>
      )}

      {page !== null && page.total === 0 && <p>No questions yet. Ask one on the Ask page.</p>}
      {page !== null && page.total > 0 && (
        <>
          <div className="table-scroll">
            <table aria-label="Question history">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  {allUsers && <th scope="col">User</th>}
                  <th scope="col">Question</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Rows</th>
                  <th scope="col">Time</th>
                  <th scope="col"><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((item) => (
                  <tr key={item.id}>
                    <td>{dateFormat.format(new Date(item.createdAt))}</td>
                    {allUsers && <td>{item.userEmail}</td>}
                    <td>{item.question}</td>
                    <td><span className={`status status-${item.status}`}>{STATUS_LABELS[item.status]}</span></td>
                    <td className="num">{item.rowCount ?? ''}</td>
                    <td className="num">{item.totalMs === null ? '' : `${(item.totalMs / 1000).toFixed(1)} s`}</td>
                    <td>
                      <button
                        type="button"
                        aria-label={`Re-run: ${item.question}`}
                        disabled={rerunning !== null}
                        onClick={() => void rerun(item)}
                      >
                        {rerunning === item.id ? 'Running…' : 'Re-run'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav className="pager" aria-label="History pages">
            <button type="button" onClick={() => { setPageNo(pageNo - 1) }} disabled={pageNo === 1}>Previous</button>
            <span>Page {pageNo} of {pages} · {page.total} questions</span>
            <button type="button" onClick={() => { setPageNo(pageNo + 1) }} disabled={pageNo >= pages}>Next</button>
          </nav>
        </>
      )}
    </>
  )
}
