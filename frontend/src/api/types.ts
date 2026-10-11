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
