import type { RequestError } from './requestError'

export function ErrorNotice({ error }: { error: RequestError }) {
  return (
    <div role="alert" className="request-error">
      <p>{error.message}</p>
      {error.correlationId !== null && <p className="muted">Reference: {error.correlationId}</p>}
    </div>
  )
}
