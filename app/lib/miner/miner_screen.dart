import 'package:flutter/material.dart';
import '../config.dart';
import '../theme.dart';
import 'miner_service.dart';

class MinerScreen extends StatefulWidget {
  const MinerScreen({super.key});
  @override
  State<MinerScreen> createState() => _MinerScreenState();
}

class _MinerScreenState extends State<MinerScreen> {
  final svc = MinerService();

  @override
  void initState() {
    super.initState();
    svc.start();
  }

  @override
  void dispose() {
    svc.dispose();
    super.dispose();
  }

  Color get _q => svc.quality == 'good'
      ? statusColor('green')
      : svc.quality == 'sweat'
          ? statusColor('amber')
          : statusColor('grey');

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: svc,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text('${Config.minerId} · ${Config.zone}')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          Row(children: [
            Expanded(child: _Big('Heart rate', svc.bpm > 0 ? svc.bpm.round().toString() : '--', 'BPM')),
            const SizedBox(width: 12),
            Expanded(child: _Big('SpO2', svc.spo2 > 0 ? svc.spo2.round().toString() : '--', '%')),
          ]),
          const SizedBox(height: 16),
          SizedBox(height: 160, child: Spark(svc.wave, color: statusColor('green'))),
          const SizedBox(height: 16),
          Wrap(spacing: 8, runSpacing: 8, children: [
            Chip(avatar: Icon(Icons.circle, size: 12, color: _q), label: Text('Signal: ${svc.quality}')),
            Chip(
                avatar: Icon(Icons.circle, size: 12, color: svc.gatewayUp ? statusColor('green') : statusColor('red')),
                label: Text(svc.gatewayUp ? 'Sensor connected' : 'Sensor offline')),
            Chip(
                avatar: Icon(Icons.circle, size: 12, color: svc.hqUp ? statusColor('green') : statusColor('amber')),
                label: Text(svc.hqUp ? 'HQ linked' : 'HQ unreachable')),
            Chip(label: Text('${svc.pending} waiting to sync')),
          ]),
          const SizedBox(height: 8),
          SwitchListTile(
            title: const Text('Simulate dead zone'),
            subtitle: const Text('Blocks sync to HQ. Readings keep saving on the phone.'),
            value: svc.deadZone,
            onChanged: svc.setDeadZone,
          ),
        ]),
      ),
    );
  }
}

class _Big extends StatelessWidget {
  final String label, value, unit;
  const _Big(this.label, this.value, this.unit);
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label),
            Text(value, style: Theme.of(context).textTheme.displayMedium),
            Text(unit),
          ]),
        ),
      );
}
