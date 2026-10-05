<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层 · 分装在架批次 · FEFO 消费走「消费」页</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>{{ label[L] }}</h3>
        <span v-for="x in by(L)" :key="x.id" class="lot">
          {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
          <em v-if="x.parent_id" class="subtag">分装子批</em>
          <Repack v-if="splittable(x)" :lot="x" @done="load" />
        </span>
        <span v-if="!by(L).length" class="muted">空层</span>
        <p class="shelf-total">本层合计：{{ itemTotals(by(L)) }}</p>
      </section>
    </div>
    <p class="grand-total">全层在架合计：{{ itemTotals(rows) }}（分装前后不变）</p>
    <button style="margin-top:12px" @click="sweep">过期下架</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import Repack from '../components/Repack.vue'
const rows = ref([])
const layers = ['upper', 'mid', 'lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
function by(L) { return rows.value.filter(r => r.layer === L) }
function r3(n) { return Math.round(n * 1000) / 1000 }
function itemTotals(list) {
  const m = new Map()
  for (const r of list) {
    const k = r.item_id
    if (!m.has(k)) m.set(k, { name: r.name, unit: r.unit, qty: 0 })
    m.get(k).qty += Number(r.qty_remain || 0)
  }
  return [...m.values()].map(t => `${t.name} ${r3(t.qty)}${t.unit || ''}`).join(' · ') || '—'
}
// Same eligibility gate as the backend: dirty / non-positive rows are not
// consume candidates, so they are not offered for repacking either.
function splittable(x) { return x.data_quality === 'clean' && Number(x.qty_remain) > 0 }
async function load() { rows.value = await api('/fridge') }
async function sweep() { await api('/expire-sweep', { method: 'POST', body: '{}' }); await load() }
onMounted(load)
</script>
