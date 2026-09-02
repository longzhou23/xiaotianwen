import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiGet, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn()
}))

vi.mock('./request', () => ({ apiGet, apiPost }))

import {
  getObservatoryEpisode,
  getObservatoryEpisodes,
  previewObservatoryReview
} from './observatory'

describe('Cognitive Observatory Episode ID transport', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  it('passes the exact listed Episode ID to the AstrBot bridge', async () => {
    const episodeId = 'episode:private:2986500364:runtime:136336110833632'
    apiGet
      .mockResolvedValueOnce({ success: true, episodes: [{ episode_id: episodeId }] })
      .mockResolvedValueOnce({ success: true, episode: { episode_id: episodeId } })

    const listing = await getObservatoryEpisodes()
    await getObservatoryEpisode(listing.episodes[0].episode_id)

    expect(apiGet).toHaveBeenNthCalledWith(1, 'cognitive-observatory/episodes', {})
    expect(apiGet).toHaveBeenNthCalledWith(
      2,
      `cognitive-observatory/episodes/${episodeId}`
    )
    expect(apiGet.mock.calls[1][0]).not.toContain('%3A')
  })

  it('uses the same unmodified ID for request-local Preview Review', async () => {
    const episodeId = 'episode:private:2986500364:runtime:136336110833632'
    apiPost.mockResolvedValue({ success: true, evidence_count: 0 })

    await previewObservatoryReview(episodeId)

    expect(apiPost).toHaveBeenCalledWith(
      `cognitive-observatory/episodes/${episodeId}/preview`,
      {}
    )
  })
})
