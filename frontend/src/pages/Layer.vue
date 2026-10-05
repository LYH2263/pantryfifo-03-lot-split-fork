<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <span v-for="x in rows" :key="x.id" class="lot">
      {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
      <em v-if="x.parent_id" class="subtag">分装子批</em>
      <Repack v-if="splittable(x)" :lot="x" @done="load" />
    </span>
    <p v-if="!rows.length" class="muted">空层</p>
    <p class="shelf-total">本层合计：{{ totals }}（与全层页该层加总一致）</p>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import Repack from '../components/Repack.vue'
const props = defineProps({ layer: String })
const rows = ref([])
function r3(n) { return Math.round(n * 1000) / 1000 }
const totals = computed(() => {
  const m = new Map()
  for (const r of rows.value) {
    if (!m.has(r.item_id)) m.set(r.item_id, { name: r.name, unit: r.unit, qty: 0 })
    m.get(r.item_id).qty += Number(r.qty_remain || 0)
  }
  return [...m.values()].map(t => `${t.name} ${r3(t.qty)}${t.unit || ''}`).join(' · ') || '—'
})
// Same eligibility gate as the backend / full-shelf page.
function splittable(x) { return x.data_quality === 'clean' && Number(x.qty_remain) > 0 }
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
