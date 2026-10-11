import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { AskResponse, Cell, ChartType } from '../api/types'
import { formatCell } from './format'

// Categorical slots in fixed order (dataviz reference palette, light mode). Validated for
// adjacent pairs across all eight; the engine never sends more than 3 series or 6 pie slices.
const SERIES_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300']
const INK = { secondary: '#52514e', muted: '#898781', grid: '#e1e0d9', axis: '#c3c2b7', surface: '#fcfcfb' }

const LABELS: Record<ChartType, string> = { bar: 'Bar', line: 'Line', pie: 'Pie', table: 'Table' }
const SIZE = { width: 720, height: 320 }

type Chart = NonNullable<AskResponse['chart']>
type Point = Record<string, string | number | null>

interface Props {
  chart: Chart
  columns: NonNullable<AskResponse['columns']>
  rows: Cell[][]
}

/**
 * The recommended chart for a result, with a switcher limited to the types the engine says the
 * data can be drawn as (design §9, R6.1, R6.2). "Table" hides the chart; the table below the
 * chart is always shown, so it doubles as the accessible view of the data.
 */
export function ResultChart({ chart, columns, rows }: Props) {
  const [type, setType] = useState<ChartType>(chart.recommended)
  const drawable = chart.x !== null && chart.y.length > 0

  if (!drawable || chart.allowed.length === 0 || (chart.allowed.length === 1 && chart.allowed[0] === 'table')) {
    return null
  }

  const data = toPoints(columns, rows, chart.x ?? '', chart.y)
  const series = chart.y.map((name, i) => ({ key: `s${String(i)}`, name, color: SERIES_COLORS[i] ?? INK.muted }))
  const title = `${LABELS[type]} chart of ${chart.y.join(', ')} by ${chart.x ?? ''}`

  return (
    <section className="result-chart" aria-label="Chart">
      <div role="group" aria-label="Chart type" className="chart-switcher">
        {chart.allowed.map((t) => (
          <button key={t} type="button" aria-pressed={t === type} onClick={() => { setType(t) }}>
            {LABELS[t]}
          </button>
        ))}
      </div>
      {type !== 'table' && (
        <ChartFigure title={title} type={type}>
          {(width) => renderChart(type, data, series, width)}
        </ChartFigure>
      )}
    </section>
  )
}

/** Measures the available width (ResizeObserver) and draws at that size; 720 px until measured. */
function ChartFigure({ title, type, children }: { title: string; type: ChartType; children: (width: number) => ReactElement }) {
  const ref = useRef<HTMLElement>(null)
  const [width, setWidth] = useState(SIZE.width)

  useEffect(() => {
    const element = ref.current
    if (element === null) return
    const observer = new ResizeObserver(([entry]) => {
      const measured = Math.floor(entry?.contentRect.width ?? 0)
      if (measured > 0) setWidth(measured)
    })
    observer.observe(element)
    return () => { observer.disconnect() }
  }, [])

  return (
    <figure ref={ref} role="img" aria-label={title} data-chart-type={type} className="chart-figure">
      {children(width)}
    </figure>
  )
}

function renderChart(type: Exclude<ChartType, 'table'>, data: Point[], series: { key: string; name: string; color: string }[], width: number) {
  const size = { width, height: SIZE.height }
  const legend = series.length > 1 ? <Legend wrapperStyle={{ color: INK.secondary }} /> : null
  const tooltip = <Tooltip formatter={(value) => formatCell(typeof value === 'number' ? value : String(value))} />
  const axes = [
    <CartesianGrid key="grid" stroke={INK.grid} vertical={false} />,
    <XAxis key="x" dataKey="x" stroke={INK.axis} tick={{ fill: INK.muted, fontSize: 12 }} />,
    <YAxis key="y" stroke={INK.axis} tick={{ fill: INK.muted, fontSize: 12 }} tickFormatter={(v: number) => formatCell(v)} />,
  ]

  switch (type) {
    case 'bar':
      return (
        <BarChart {...size} data={data} barGap={2} barCategoryGap="20%">
          {axes}
          {tooltip}
          {legend}
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          ))}
        </BarChart>
      )
    case 'line':
      return (
        <LineChart {...size} data={data}>
          {axes}
          {tooltip}
          {legend}
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2}
              dot={{ r: 4, fill: s.color, stroke: INK.surface, strokeWidth: 2 }} isAnimationActive={false} />
          ))}
        </LineChart>
      )
    case 'pie': {
      const first = series[0]
      if (first === undefined) return <PieChart {...size} />
      return (
        <PieChart {...size}>
          {tooltip}
          <Legend wrapperStyle={{ color: INK.secondary }} />
          {/* Each slice takes its slot colour from its data point's fill, in fixed order. */}
          <Pie data={data.map((point, i) => ({ ...point, fill: SERIES_COLORS[i] ?? INK.muted }))}
            dataKey={first.key} nameKey="x" name={first.name} outerRadius={120}
            stroke={INK.surface} strokeWidth={2} isAnimationActive={false} />
        </PieChart>
      )
    }
  }
}

/** Rows to chart points: x is the category/time label, s0..s2 the measures (by index, so any column name is safe). */
function toPoints(columns: Props['columns'], rows: Cell[][], x: string, y: string[]): Point[] {
  const index = new Map(columns.map((c, i) => [c.name, i]))
  const xi = index.get(x) ?? 0
  const yi = y.map((name) => index.get(name) ?? -1)
  return rows.map((row) => {
    const point: Point = { x: formatCell(row[xi] ?? null) }
    yi.forEach((col, i) => {
      const value = col < 0 ? null : (row[col] ?? null)
      point[`s${String(i)}`] = typeof value === 'number' ? value : null
    })
    return point
  })
}
