<template>
  <section class="observatory">
    <v-card class="hero mb-3" variant="flat">
      <v-card-text class="py-5">
        <div class="d-flex align-start flex-wrap ga-3">
          <div>
            <div class="text-h5 font-weight-bold">小天文 Cognitive Observatory</div>
            <div class="text-body-2 text-medium-emphasis">P1 Experience &amp; Review Foundation</div>
          </div>
          <v-spacer />
          <v-chip color="success" variant="flat" prepend-icon="mdi-shield-check">P1 FOUNDATION · ACCEPTED</v-chip>
          <v-chip color="amber-darken-2" variant="tonal" prepend-icon="mdi-gate">Promotion · DISABLED</v-chip>
        </div>
        <v-alert color="amber-darken-2" variant="tonal" density="compact" class="mt-4 mb-0">
          当前 Review 可以形成可审计 Finding，但尚未冻结安全的 Evidence promotion contract，因此不会自动形成长期学习证据。
          <strong>Evidence = 0 是有意的 fail-closed 安全门。</strong>
        </v-alert>
      </v-card-text>
    </v-card>

    <v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>

    <v-row class="mb-1">
      <v-col v-for="card in summaryCards" :key="card.label" cols="6" sm="4" md="2">
        <v-card variant="flat" class="metric pa-3"><div class="text-caption text-medium-emphasis">{{ card.label }}</div><div class="text-h5 font-weight-bold">{{ card.value }}</div></v-card>
      </v-col>
    </v-row>

    <v-card v-if="!summary?.available" variant="flat" class="pa-7 text-center mb-3">
      <v-icon icon="mdi-database-off-outline" size="44" color="medium-emphasis" />
      <div class="text-h6 mt-2">EpisodeStore 尚未接入运行时</div>
      <div class="text-body-2 text-medium-emphasis mt-1">这不是数据错误。可使用下方内存 Demo 演示已冻结的 P1 观察与 Review 语义。</div>
    </v-card>

    <v-row>
      <v-col cols="12" lg="4">
        <v-card variant="flat" class="panel fill-height">
          <v-card-title class="d-flex align-center text-subtitle-1"><v-icon icon="mdi-view-list-outline" class="mr-2" />Episodes<v-spacer /><v-btn icon="mdi-refresh" variant="text" size="small" @click="loadAll" /></v-card-title>
          <v-card-text class="pt-0">
            <v-text-field v-model="query" label="搜索 Episode / Root / Ref" density="compact" hide-details clearable prepend-inner-icon="mdi-magnify" class="mb-2" @keyup.enter="loadEpisodes" />
            <v-btn-toggle v-model="state" density="compact" variant="tonal" class="state-toggle mb-3" @update:model-value="loadEpisodes"><v-btn value="ALL">ALL</v-btn><v-btn value="OPEN">OPEN</v-btn><v-btn value="SOFT_CLOSED">SOFT</v-btn><v-btn value="FINALIZED">FINALIZED</v-btn><v-btn value="INTERRUPTED">INTERRUPTED</v-btn></v-btn-toggle>
            <v-list v-if="episodes.length" density="compact" class="episode-list">
              <v-list-item v-for="episode in episodes" :key="episode.episode_id" :active="selectedId === episode.episode_id" @click="selectEpisode(episode.episode_id)">
                <template #prepend><v-icon :color="stateColor(episode.state)" icon="mdi-circle" size="10" /></template>
                <v-list-item-title class="text-body-2 text-truncate">{{ episode.episode_id }}</v-list-item-title>
                <v-list-item-subtitle>{{ episode.state }} · {{ episode.event_count }} events · {{ episode.outcome_count }} outcomes</v-list-item-subtitle>
              </v-list-item>
            </v-list>
            <div v-else class="text-center text-body-2 text-medium-emphasis py-6">{{ loading ? '正在读取…' : 'No data yet' }}</div>
          </v-card-text>
        </v-card>

        <v-card variant="flat" class="panel mt-3">
          <v-card-title class="text-subtitle-1"><v-icon icon="mdi-flask-outline" class="mr-2" />Demo Cases <v-chip size="x-small" class="ml-2" color="info">IN-MEMORY ONLY</v-chip></v-card-title>
          <v-list density="compact"><v-list-item v-for="item in demos" :key="item.id" @click="selectDemo(item.id)"><v-list-item-title>{{ item.title }}</v-list-item-title><v-list-item-subtitle>{{ item.summary }}</v-list-item-subtitle></v-list-item></v-list>
        </v-card>
      </v-col>

      <v-col cols="12" lg="8">
        <v-card variant="flat" class="panel min-detail">
          <v-card-text v-if="!detail" class="py-16 text-center text-medium-emphasis"><v-icon size="48" icon="mdi-eye-outline" /><div class="mt-2">选择一个 Episode 或 Demo Case 查看不可变历史。</div></v-card-text>
          <template v-else>
            <v-card-title class="d-flex flex-wrap align-center ga-2"><span class="text-subtitle-1">{{ detail.episode.episode_id }}</span><v-chip size="small" :color="stateColor(detail.episode.state)">{{ detail.episode.state }}</v-chip><v-chip v-if="isDemo" size="small" color="info">DEMO DATA</v-chip><v-spacer /><v-btn color="primary" variant="tonal" size="small" prepend-icon="mdi-play-circle-outline" :disabled="isDemo || !detail.episode.finalized_at" @click="preview">Preview Review</v-btn></v-card-title>
            <v-card-subtitle>Root: {{ detail.episode.root_event_id }} · Started {{ formatTime(detail.episode.opened_at) }} · Finalized {{ formatTime(detail.episode.finalized_at) }}</v-card-subtitle>
            <v-card-text>
              <v-alert v-if="isDemo" color="info" variant="tonal" density="compact" class="mb-3">DEMO DATA 仅在本次请求内存中构造；不会进入真实 EpisodeStore、ReviewStore、Iris 或未来行为。</v-alert>
              <div class="pipeline mb-4"><span>Raw Event</span><v-icon icon="mdi-arrow-right" /><span>Canonical Experience</span><v-icon icon="mdi-arrow-right" /><span>Episode</span><v-icon icon="mdi-arrow-right" /><span>Behavior / Host</span><v-icon icon="mdi-arrow-right" /><span>Outcome</span><v-icon icon="mdi-arrow-right" /><span>ReviewFinding</span><v-icon icon="mdi-arrow-right" /><strong>Promotion Gate · CLOSED</strong></div>
              <v-row>
                <v-col cols="12" md="7"><div class="section-label">Immutable Timeline</div><v-timeline density="compact" side="end" truncate-line="both"><v-timeline-item v-for="event in detail.timeline" :key="event.kind + event.ref_id" size="x-small" :dot-color="event.late_feedback ? 'amber-darken-2' : 'primary'"><div class="text-caption text-medium-emphasis">{{ formatTime(event.at) }}</div><div class="text-body-2 font-weight-medium">{{ event.kind }}</div><div class="text-caption text-medium-emphasis word-break">{{ event.ref_id }}</div><v-chip v-if="event.late_feedback" size="x-small" color="amber-darken-2" variant="tonal">LATE FEEDBACK</v-chip></v-timeline-item></v-timeline></v-col>
                <v-col cols="12" md="5"><div class="section-label">Fact attachment</div><v-card v-for="item in detail.attachments" :key="item.ref_id" variant="outlined" class="mb-2 pa-2"><div class="d-flex align-center"><v-chip size="x-small" :color="attachmentColor(item.status)">{{ item.status }}</v-chip><span class="text-caption ml-2">{{ item.source_type }}</span></div><div class="text-caption word-break mt-1">{{ item.ref_id }}</div><div v-if="item.reason" class="text-caption text-medium-emphasis">{{ item.reason }}</div></v-card><div class="text-caption text-medium-emphasis">状态由后端实际 P1 snapshot validation 产生；前端不自行重算。</div></v-col>
              </v-row>

              <div class="section-label mt-3">Outcomes</div><v-card v-if="!detail.outcomes.length" variant="outlined" class="pa-3 text-body-2 text-medium-emphasis">No outcomes observed. Absence is not negative feedback.</v-card><v-card v-for="outcome in detail.outcomes" :key="outcome.observation_id" variant="outlined" class="mb-2 pa-3"><div class="d-flex align-center ga-2"><strong>{{ outcome.kind }}</strong><v-chip v-if="outcome.late_feedback" size="x-small" color="amber-darken-2">LATE FEEDBACK</v-chip></div><div class="text-caption word-break">{{ outcome.observation_id }} · target {{ outcome.target_episode_id }}</div><div class="text-caption text-medium-emphasis">observed {{ formatTime(outcome.observed_at) }} · explicitness {{ outcome.explicitness }} · confidence {{ outcome.confidence }}</div></v-card>

              <div class="section-label mt-4">Persisted Review</div><v-card variant="outlined" class="pa-3"><template v-if="detail.review.status === 'AVAILABLE'"><div class="text-body-2">{{ detail.review.runs.length }} immutable ReviewRun(s) available; Evidence count {{ detail.review.evidence.length }}.</div><v-chip v-for="run in detail.review.runs" :key="run.review_run_id" size="x-small" class="mr-1 mt-2" color="success">{{ run.status }} · {{ run.findings.length }} findings</v-chip></template><div v-else class="text-body-2 text-medium-emphasis">{{ detail.review.status === 'NOT_WIRED' ? 'No persisted ReviewRuns: ReviewStore is not wired in this runtime.' : 'No persisted review for this Episode.' }}</div></v-card>

              <v-divider class="my-4" /><div class="section-label">Snapshot Integrity</div><v-row dense><v-col cols="12" md="7"><v-card variant="outlined" class="pa-3"><div class="text-caption text-medium-emphasis">Input Snapshot Hash</div><div class="text-body-2 word-break">{{ detail.snapshot.hash || detail.snapshot.reason || 'Unavailable' }}</div></v-card></v-col><v-col cols="12" md="5"><v-card variant="outlined" class="pa-3"><div>FACT_PAYLOAD_HASHED <strong>YES</strong></div><div>FACT_DEEP_SNAPSHOTTED <strong>YES</strong></div><div>EPISODE_ATTACHMENT <strong>ENFORCED</strong></div></v-card></v-col></v-row>

              <v-card variant="tonal" color="amber-darken-2" class="promotion mt-4 pa-4"><div class="text-subtitle-2">ReviewEvidence Promotion</div><div class="d-flex align-center mt-2"><span>ReviewFinding</span><v-icon icon="mdi-arrow-right" class="mx-2" /><v-chip color="amber-darken-2">Promotion Gate · CLOSED</v-chip><v-icon icon="mdi-close" class="mx-2" /><span>ReviewEvidence</span></div><div class="text-body-2 mt-2">Evidence produced: <strong>0</strong> — no frozen promotion rule currently authorizes ReviewFinding → ReviewEvidence.</div></v-card>

              <v-card v-if="previewResult" variant="outlined" class="mt-4 pa-3"><div class="d-flex align-center"><div class="section-label mb-0">Preview Review</div><v-chip class="ml-2" size="x-small" color="info">PREVIEW ONLY · NOT PERSISTED</v-chip></div><div class="text-body-2 mt-2">Eligibility: <strong>{{ previewResult.eligibility.decision }}</strong> · {{ previewResult.eligibility.reason }}</div><v-alert v-if="previewResult.unavailable_reason" density="compact" variant="tonal" type="info" class="mt-2">完整 execution record 未接入运行时；为避免伪造 Host facts，本次 Preview 被安全拒绝。</v-alert><template v-if="previewResult.run"><div class="text-caption word-break mt-2">{{ previewResult.run.review_run_id }} · {{ previewResult.run.status }} · {{ previewResult.run.input_snapshot_hash }}</div><v-card v-for="finding in previewResult.run.findings" :key="finding.finding_id" variant="tonal" class="mt-2 pa-3"><strong>{{ finding.finding_type }}</strong> · {{ finding.dimension }}<div class="mt-1">{{ finding.claim }}</div><div class="text-caption">confidence {{ finding.confidence }} · causal {{ finding.causal_attribution }}</div><v-alert v-if="finding.claim.includes('contradicted')" density="compact" variant="outlined" class="mt-2">这表示系统观察到用户显式反驳了该输出；不证明 Host 客观错误、Grounding 失败、应调用工具、长期偏好，且不会改变未来行为。</v-alert><v-alert v-if="finding.claim.includes('acknowledgement')" density="compact" variant="outlined" class="mt-2">这只表示 Host 输出之后出现显式 acknowledgement；不表示成功、奖励、偏好或正向质量。</v-alert></v-card></template></v-card>
              <v-expansion-panels class="mt-4"><v-expansion-panel title="Canonical facts / provenance · read-only"><v-expansion-panel-text><pre>{{ pretty(detail.snapshot.canonical_facts || { status: detail.snapshot.status || 'No complete execution fact carrier wired' }) }}</pre></v-expansion-panel-text></v-expansion-panel><v-expansion-panel title="Raw JSON · read-only"><v-expansion-panel-text><pre>{{ pretty(detail.raw) }}</pre></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
            </v-card-text>
          </template>
        </v-card>
      </v-col>
    </v-row>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getObservatoryDemoCase, getObservatoryDemoCases, getObservatoryEpisode, getObservatoryEpisodes, getObservatorySummary, previewObservatoryReview } from '@/api/observatory'

const summary = ref<any>(null); const episodes = ref<any[]>([]); const demos = ref<any[]>([]); const detail = ref<any>(null); const previewResult = ref<any>(null)
const selectedId = ref(''); const query = ref(''); const state = ref('ALL'); const loading = ref(false); const error = ref(''); const isDemo = ref(false)
const summaryCards = computed(() => [{ label: 'Episodes', value: summary.value?.episodes ?? '—' }, { label: 'Finalized', value: summary.value?.finalized_episodes ?? '—' }, { label: 'Outcomes', value: summary.value?.outcomes ?? '—' }, { label: 'Review Runs', value: summary.value?.review_runs ?? '—' }, { label: 'Findings', value: summary.value?.review_findings ?? '—' }, { label: 'Evidence', value: summary.value?.review_evidence ?? 0 }])
const stateColor = (value: string) => value === 'FINALIZED' ? 'success' : value === 'OPEN' ? 'primary' : 'grey'
const attachmentColor = (value: string) => value === 'ATTACHED' ? 'success' : value === 'REJECTED' ? 'error' : 'grey'
const formatTime = (value?: string) => value ? value.replace('T', ' ').replace('+00:00', ' UTC') : '—'
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
async function loadEpisodes() { loading.value = true; try { const result = await getObservatoryEpisodes({ state: state.value, query: query.value, limit: 50 }); episodes.value = result.episodes || [] } catch (e: any) { error.value = e.message || '读取 Episode 失败' } finally { loading.value = false } }
async function loadAll() { error.value = ''; await Promise.all([getObservatorySummary().then(v => summary.value = v), getObservatoryDemoCases().then(v => demos.value = v), loadEpisodes()]).catch((e: any) => error.value = e.message || '加载失败') }
async function selectEpisode(id: string) { selectedId.value = id; isDemo.value = false; previewResult.value = null; try { detail.value = await getObservatoryEpisode(id) } catch (e: any) { error.value = e.message || '读取详情失败' } }
async function selectDemo(id: string) { selectedId.value = ''; isDemo.value = true; previewResult.value = null; try { const result = await getObservatoryDemoCase(id); detail.value = result.detail; previewResult.value = result.preview } catch (e: any) { error.value = e.message || '读取 Demo 失败' } }
async function preview() { if (!selectedId.value) return; try { previewResult.value = await previewObservatoryReview(selectedId.value) } catch (e: any) { error.value = e.message || 'Preview 失败' } }
onMounted(loadAll)
</script>

<style scoped>
.hero { background: linear-gradient(120deg, rgba(21, 101, 192, .12), rgba(0, 137, 123, .08)); border: 1px solid rgba(var(--v-theme-primary), .13); }.metric,.panel { border: 1px solid rgba(var(--v-theme-on-surface), .08); }.episode-list { max-height: 410px; overflow: auto; }.state-toggle { max-width: 100%; overflow-x: auto; }.min-detail { min-height: 650px; }.pipeline { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; font-size: .78rem; color: rgba(var(--v-theme-on-surface), .7); }.pipeline strong { color: rgb(var(--v-theme-warning)); }.section-label { font-size: .88rem; font-weight: 700; margin-bottom: 8px; }.word-break { word-break: break-all; } pre { max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: .76rem; background: rgba(var(--v-theme-on-surface), .05); padding: 10px; border-radius: 6px; } @media (max-width: 600px) { .pipeline { display: none; } }
</style>
