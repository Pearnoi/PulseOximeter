import 'dart:async';
import 'package:flutter/material.dart';
import '../models.dart';
import '../theme.dart';
import 'hq_service.dart';

/// Zoomed-in view of one miner. Live numbers come from the HQ snapshot;
/// the charts come from the last 15 minutes of history on the server.
class MinerDetailScreen extends StatefulWidget {
  final HqService svc;
  final String minerId;
  const MinerDetailScreen({super.key, required this.svc, required this.minerId});
  @override
  State<MinerDetailScreen> createState() => _MinerDetailScreenState();
}

class _MinerDetailScreenState extends State<MinerDetailScreen> {
  List<Map<String, dynamic>> points = [];
  Timer? _t;

  @override
  void initState() {
    super.initState();
    _load();
    _t = Timer.periodic(const Duration(seconds: 2), (_) => _load());
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final p = await widget.svc.history(widget.minerId);
      if (mounted) setState(() => points = p);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.svc,
      builder: (context, _) {
        final miners = widget.svc.snap?.miners.where((m) => m.id == widget.minerId);
        final m = (miners == null || miners.isEmpty) ? null : miners.first;
        return Scaffold(
          appBar: AppBar(title: Text(m == null ? widget.minerId : '${m.name} · ${m.zone}')),
          body: m == null
              ? const Center(child: Text('Waiting for data...'))
              : _body(context, m),
        );
      },
    );
  }

  Widget _body(BuildContext context, Miner m) {
    final c = statusColor(m.status);
    final bpms = points.map((p) => (p['bpm'] as num).toDouble()).toList();
    final spos = points.map((p) => (p['spo2'] as num).toDouble()).toList();
    final avg = bpms.isEmpty ? 0 : bpms.reduce((a, b) => a + b) / bpms.length;
    final maxHr = bpms.isEmpty ? 0 : bpms.reduce((a, b) => a > b ? a : b);
    final minSp = spos.isEmpty ? 0 : spos.reduce((a, b) => a < b ? a : b);

    return ListView(padding: const EdgeInsets.all(16), children: [
      Wrap(spacing: 8, runSpacing: 8, children: [
        Chip(avatar: Icon(Icons.circle, size: 12, color: c), label: Text(m.status.toUpperCase())),
        if (m.cause != null) Chip(label: Text('Cause: ${m.cause}')),
        Chip(label: Text('Signal: ${m.quality}')),
        Chip(label: Text('Trend ${m.trendArrow}')),
        if (m.real) const Chip(avatar: Icon(Icons.sensors, size: 16), label: Text('Real sensor')),
      ]),
      if (m.status == 'grey')
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(
            m.real
                ? 'No data from this miner. Check that the miner app is open and connected to the sensor (or fake_gateway.py).'
                : 'Signal lost.',
            style: TextStyle(color: statusColor('amber')),
          ),
        ),
      const SizedBox(height: 12),
      Row(children: [
        Expanded(child: _Big('Heart rate', m.bpm > 0 ? m.bpm.round().toString() : '--', 'BPM', c)),
        const SizedBox(width: 12),
        Expanded(child: _Big('SpO2', m.spo2 > 0 ? m.spo2.round().toString() : '--', '%', c)),
      ]),
      const SizedBox(height: 16),
      Text('Heart rate, last 15 min', style: Theme.of(context).textTheme.titleSmall),
      SizedBox(height: 140, child: Spark(bpms, color: c)),
      const SizedBox(height: 16),
      Text('SpO2, last 15 min', style: Theme.of(context).textTheme.titleSmall),
      SizedBox(height: 140, child: Spark(spos, color: c)),
      const SizedBox(height: 16),
      Wrap(spacing: 12, runSpacing: 12, children: [
        _Stat('Avg HR', avg > 0 ? avg.round().toString() : '--'),
        _Stat('Max HR', maxHr > 0 ? maxHr.round().toString() : '--'),
        _Stat('Lowest SpO2', minSp > 0 ? '${minSp.round()}%' : '--'),
        _Stat('Fatigue', '${m.fatigue.round()}%'),
        _Stat('Hypoxia risk', '${m.hypoxia.round()}%'),
        _Stat('Readings', '${points.length}'),
      ]),
    ]);
  }
}

class _Big extends StatelessWidget {
  final String label, value, unit;
  final Color color;
  const _Big(this.label, this.value, this.unit, this.color);
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label),
            Text(value, style: Theme.of(context).textTheme.displayMedium?.copyWith(color: color)),
            Text(unit),
          ]),
        ),
      );
}

class _Stat extends StatelessWidget {
  final String label, value;
  const _Stat(this.label, this.value);
  @override
  Widget build(BuildContext context) => SizedBox(
        width: 130,
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(label, style: Theme.of(context).textTheme.labelMedium),
              Text(value, style: Theme.of(context).textTheme.titleLarge),
            ]),
          ),
        ),
      );
}
