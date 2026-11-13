<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink to="/chat" class="brand">
        <span class="brand-mark">D</span>
        <span><strong>财报分析助手</strong><small>dubonline · 财报取证与计算复核</small></span>
      </RouterLink>
      <nav class="topnav"><RouterLink to="/chat" class="active">Agent 对话</RouterLink></nav>
      <div class="user-chip"><span></span> dubonline</div>
    </header>
    <main class="app-main"><RouterView /></main>
  </div>
</template>
