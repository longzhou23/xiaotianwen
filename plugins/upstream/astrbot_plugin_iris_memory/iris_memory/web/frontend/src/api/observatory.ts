import { apiGet, apiPost } from './request'

interface ApiResponse { success: boolean; error?: string }

function ensure(response: ApiResponse, fallback: string) {
  if (!response.success) throw new Error(response.error || fallback)
}

export async function getObservatorySummary(): Promise<any> {
  const response = await apiGet<any>('cognitive-observatory/summary')
  ensure(response, '获取认知观测台摘要失败')
  return response.summary
}

export async function getObservatoryEpisodes(params: Record<string, any> = {}): Promise<any> {
  const response = await apiGet<any>('cognitive-observatory/episodes', params)
  ensure(response, '获取 Episode 列表失败')
  return response
}

export async function getObservatoryEpisode(id: string): Promise<any> {
  const response = await apiGet<any>(`cognitive-observatory/episodes/${encodeURIComponent(id)}`)
  ensure(response, '获取 Episode 详情失败')
  return response
}

export async function previewObservatoryReview(id: string): Promise<any> {
  const response = await apiPost<any>(`cognitive-observatory/episodes/${encodeURIComponent(id)}/preview`, {})
  ensure(response, '预览 Review 失败')
  return response
}

export async function getObservatoryDemoCases(): Promise<any[]> {
  const response = await apiGet<any>('cognitive-observatory/demo-cases')
  ensure(response, '获取 Demo Cases 失败')
  return response.cases || []
}

export async function getObservatoryDemoCase(id: string): Promise<any> {
  const response = await apiGet<any>(`cognitive-observatory/demo-cases/${encodeURIComponent(id)}`)
  ensure(response, '获取 Demo Case 失败')
  return response
}
