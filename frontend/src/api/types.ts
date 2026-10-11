// Public API shapes (design §4.2). Grows with each page; T40 needs auth and the schema summary.

export type Role = 'user' | 'admin'

export interface User {
  id: string
  email: string
  role: Role
}

export interface LoginResponse {
  accessToken: string
  expiresAt: string
  user: User
}

/** Error envelope returned by every non-2xx response (design §4.1). */
export interface ErrorEnvelope {
  error: {
    code: string
    message: string
    correlationId: string | null
  }
}

export type AskStatus = 'success' | 'failed' | 'blocked' | 'cannot_answer'

export type ChartType = 'bar' | 'line' | 'pie' | 'table'

/** A result cell as encoded by the engine (design §4.2): numbers, strings (incl. ISO dates), booleans, null. */
export type Cell = string | number | boolean | null

export interface AskColumn {
  name: string
  type: 'integer' | 'numeric' | 'text' | 'boolean' | 'date' | 'timestamp' | 'other'
  dbType: string
}

export interface AskAttempt {
  n: number
  sql: string | null
  errorCode: string | null
  error: string | null
  stage: 'extract' | 'validate' | 'execute'
  latencyMs: number
}

/** POST /api/ask (design §4.2). Result fields are null unless status is success. */
export interface AskResponse {
  historyId: string
  correlationId: string
  status: AskStatus
  question: string
  sql: string | null
  columns: AskColumn[] | null
  rows: Cell[][] | null
  rowCount: number | null
  truncated: boolean | null
  chart: { recommended: ChartType; allowed: ChartType[]; x: string | null; y: string[] } | null
  summary: string | null
  message: string | null
  attempts: AskAttempt[]
  timings: {
    linkingMs: number | null
    generationMs: number | null
    validationMs: number | null
    executionMs: number | null
    summaryMs: number | null
    engineTotalMs: number | null
    totalMs: number
  }
  usage: { model: string | null; promptTokens: number | null; completionTokens: number | null } | null
}

export interface SchemaResponse {
  schemaHash: string
  introspectedAt: string
  dialect: string
  tables: {
    name: string
    columns: { name: string; type: string; nullable: boolean; primaryKey: boolean; samples: string[] }[]
    foreignKeys: { columns: string[]; refTable: string; refColumns: string[] }[]
  }[]
}
