<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <p class="muted">本层合计 {{ total }}</p>
    <span v-for="x in rows" :key="x.id" class="lot">
      #{{ x.id }} {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}<template v-if="x.parent_id"> · 拆自#{{ x.parent_id }}</template>
      <button class="mini" @click="split(x)">分装</button>
    </span>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { splitLot } from '../split'
const props = defineProps({ layer: String })
const rows = ref([])
const total = computed(() => Math.round(rows.value.reduce((s, r) => s + Number(r.qty_remain), 0) * 1000) / 1000)
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
async function split(x) { if (await splitLot(x)) await load() }
watch(() => props.layer, load)
onMounted(load)
</script>
