import 'package:flutter/material.dart';
import '../models.dart';
import '../theme.dart';
import 'hq_service.dart';
import 'miner_detail_screen.dart';

class HqScreen extends StatefulWidget {
  const HqScreen({super.key});
  @override
  State<HqScreen> createState() => _HqScreenState();
}

class _HqScreenState extends State<HqScreen> {
  final svc = HqService();

  @override
  void initState() {
    super.initState();
    svc.connect();
  }

  @override
  void dispose() {
    svc.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: svc,
      builder: (context, _) {
        final s = svc.snap;
        return Scaffold(
          appBar: AppBar(
            title: const Text('Control room'),
            actions: [
              TextButton.icon(
                icon: const Icon(Icons.sensors),
                label: const Text('Miner 01 (live sensor)'),
                onPressed: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => MinerDetailScreen(svc: svc, minerId: 'MINER-01'))),
              ),
              const SizedBox(width: 8),
              Icon(Icons.circle, size: 12, color: statusColor(svc.connected ? 'green' : 'red')),
              const SizedBox(width: 8),
              Builder(
                  builder: (ctx) => IconButton(
                      tooltip: 'Simulation panel',
                      icon: const Icon(Icons.science_outlined),
                      onPressed: () => Scaffold.of(ctx).openEndDrawer())),
            ],
          ),
          endDrawer: _SimDrawer(svc),
          body: s == null
              ? const Center(child: Text('Connecting to HQ server...'))
              : Column(children: [
                  _KpiBar(s.kpi),
                  Expanded(
                    child: Row(children: [
                      Expanded(
                        flex: 3,
                        child: Column(children: [
                          Expanded(flex: 3, child: _ZoneMap(s)),
                          Expanded(flex: 2, child: _AlertLog(s.alerts, svc)),
                        ]),
                      ),
                      Expanded(flex: 2, child: _Roster(s.miners, svc)),
                    ]),
                  ),
                ]),
        );
      },
    );
  }
}

class _KpiBar extends StatelessWidget {
  final Kpi k;
  const _KpiBar(this.k);
  @override
  Widget build(BuildContext context) {
    Widget tile(String label, String value, {Color? color}) => Expanded(
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(label, style: Theme.of(context).textTheme.labelMedium),
                Text(value, style: Theme.of(context).textTheme.headlineSmall?.copyWith(color: color)),
              ]),
            ),
          ),
        );
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(children: [
        tile('Accounted for', '${k.accounted} / ${k.total}',
            color: k.accounted < k.total ? statusColor('amber') : null),
        tile('Critical', '${k.critical}', color: k.critical > 0 ? statusColor('red') : null),
        tile('Warnings', '${k.warnings}', color: k.warnings > 0 ? statusColor('amber') : null),
        tile('Avg crew HR', k.avgHr.round().toString()),
        tile('Lowest SpO2', k.minSpo2 > 0 ? '${k.minSpo2.round()}%' : '--',
            color: k.minSpo2 > 0 && k.minSpo2 < 94 ? statusColor('red') : null),
      ]),
    );
  }
}

class _ZoneMap extends StatelessWidget {
  final Snapshot s;
  const _ZoneMap(this.s);

  Widget _zone(BuildContext context, String name) {
    final a = s.ambient[name]!;
    final here = s.miners.where((m) => m.zone == name).toList();
    return Expanded(
      child: Container(
        margin: const EdgeInsets.all(6),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: a.danger ? statusColor('red') : Colors.white24, width: a.danger ? 3 : 1),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(name, style: Theme.of(context).textTheme.titleMedium),
          Text('O2 ${a.o2.toStringAsFixed(1)}%  CO ${a.co.round()} ppm  CH4 ${a.ch4.toStringAsFixed(1)}%',
              style: TextStyle(color: a.danger ? statusColor('red') : Colors.white60)),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final m in here)
              Tooltip(
                message: '${m.name}  ${m.bpm.round()} bpm  ${m.spo2.round()}%',
                child: CircleAvatar(radius: 10, backgroundColor: statusColor(m.status)),
              ),
          ]),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(6),
        child: Column(children: [
          Expanded(child: Row(children: [_zone(context, 'Shaft Entry'), _zone(context, 'Level 2 Haulage')])),
          Expanded(child: Row(children: [_zone(context, 'Stope A'), _zone(context, 'Refuge Chamber')])),
        ]),
      );
}

class _Roster extends StatelessWidget {
  final List<Miner> miners;
  final HqService svc;
  const _Roster(this.miners, this.svc);
  @override
  Widget build(BuildContext context) {
    final sorted = [...miners]..sort((a, b) => a.rank.compareTo(b.rank));
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: sorted.length,
      itemBuilder: (_, i) {
        final m = sorted[i];
        final c = statusColor(m.status);
        return Card(
          clipBehavior: Clip.antiAlias,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(8), side: BorderSide(color: c, width: m.rank <= 1 ? 2 : 0.5)),
          child: InkWell(
            onTap: () => Navigator.push(
                context, MaterialPageRoute(builder: (_) => MinerDetailScreen(svc: svc, minerId: m.id))),
            child: Padding(
            padding: const EdgeInsets.all(10),
            child: Row(children: [
              Expanded(
                flex: 3,
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(m.name + (m.real ? ' (live)' : ''), style: const TextStyle(fontWeight: FontWeight.bold)),
                  Text(m.zone, style: const TextStyle(color: Colors.white60)),
                  const SizedBox(height: 4),
                  Text(m.status == 'grey'
                      ? 'Signal lost'
                      : '${m.bpm.round()} BPM ${m.trendArrow}   ${m.spo2.round()}%'),
                  if (m.cause != null) Text(m.cause!, style: TextStyle(color: c)),
                  const SizedBox(height: 4),
                  LinearProgressIndicator(value: m.fatigue / 100, color: c, minHeight: 3),
                ]),
              ),
              const SizedBox(width: 12),
              Expanded(flex: 2, child: SizedBox(height: 50, child: Spark(m.spark, color: c))),
            ]),
          ),
          ),
        );
      },
    );
  }
}

class _AlertLog extends StatelessWidget {
  final List<AlertItem> alerts;
  final HqService svc;
  const _AlertLog(this.alerts, this.svc);
  @override
  Widget build(BuildContext context) {
    if (alerts.isEmpty) return const Center(child: Text('No alerts. Everyone is within normal range.'));
    return ListView(padding: const EdgeInsets.all(8), children: [
      for (final a in alerts)
        Card(
          child: ListTile(
            leading: Icon(a.severity == 'critical' ? Icons.error : Icons.warning_amber,
                color: statusColor(a.severity == 'critical' ? 'red' : 'amber')),
            title: Text(a.message),
            subtitle: Text(a.action ?? (a.ack ? 'Acknowledged' : (a.cause ?? ''))),
            trailing: a.ack
                ? null
                : TextButton(onPressed: () => svc.ack(a.id), child: const Text('Acknowledge')),
          ),
        ),
    ]);
  }
}

class _SimDrawer extends StatelessWidget {
  final HqService svc;
  const _SimDrawer(this.svc);
  @override
  Widget build(BuildContext context) {
    Widget item(String label, String key, IconData icon) => ListTile(
          leading: Icon(icon),
          title: Text(label),
          onTap: () => svc.sim(key),
        );
    return Drawer(
      child: SafeArea(
        child: ListView(children: [
          const Padding(padding: EdgeInsets.all(16), child: Text('Simulation panel', style: TextStyle(fontSize: 20))),
          item('Simulate gas leak in Stope A', 'gas_leak', Icons.cloud),
          item('Simulate miner overexertion', 'overexert', Icons.directions_run),
          item('Simulate Wi-Fi dead zone', 'deadzone', Icons.wifi_off),
          const Divider(),
          item('Reset everything', 'reset', Icons.restart_alt),
        ]),
      ),
    );
  }
}
