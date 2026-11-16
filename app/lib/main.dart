import 'package:flutter/material.dart';
import 'config.dart';
import 'hq/hq_screen.dart';
import 'miner/miner_screen.dart';
import 'theme.dart';

void main() => runApp(MaterialApp(
      title: 'MinerGuard',
      theme: appTheme,
      debugShowCheckedModeBanner: false,
      home: const RoleScreen(),
    ));

class RoleScreen extends StatefulWidget {
  const RoleScreen({super.key});
  @override
  State<RoleScreen> createState() => _RoleScreenState();
}

class _RoleScreenState extends State<RoleScreen> {
  final gw = TextEditingController(text: Config.gateway);
  final hq = TextEditingController(text: Config.hq);
  final id = TextEditingController(text: Config.minerId);

  void _go(Widget screen) {
    Config.gateway = gw.text.trim();
    Config.hq = hq.text.trim();
    Config.minerId = id.text.trim();
    Navigator.push(context, MaterialPageRoute(builder: (_) => screen));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: ListView(shrinkWrap: true, padding: const EdgeInsets.all(24), children: [
            Text('MinerGuard', style: Theme.of(context).textTheme.headlineLarge),
            const SizedBox(height: 24),
            TextField(controller: gw, decoration: const InputDecoration(labelText: 'Gateway (Pear\'s Python) host:port')),
            TextField(controller: hq, decoration: const InputDecoration(labelText: 'HQ server host:port')),
            TextField(controller: id, decoration: const InputDecoration(labelText: 'Miner ID')),
            const SizedBox(height: 24),
            FilledButton(onPressed: () => _go(const MinerScreen()), child: const Text('Open miner app')),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: () => _go(const HqScreen()), child: const Text('Open HQ dashboard')),
          ]),
        ),
      ),
    );
  }
}
