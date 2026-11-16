class Miner {
  final String id, name, zone, quality, status, trend;
  final String? cause;
  final bool real;
  final double bpm, spo2, fatigue, hypoxia;
  final List<double> spark;

  Miner.fromJson(Map<String, dynamic> j)
      : id = j['id'] as String,
        name = j['name'] as String,
        zone = j['zone'] as String,
        quality = j['quality'] as String,
        status = j['status'] as String,
        trend = j['trend'] as String,
        cause = j['cause'] as String?,
        real = j['real'] as bool,
        bpm = (j['bpm'] as num).toDouble(),
        spo2 = (j['spo2'] as num).toDouble(),
        fatigue = (j['fatigue'] as num).toDouble(),
        hypoxia = (j['hypoxia'] as num).toDouble(),
        spark = (j['spark'] as List).map((e) => (e as num).toDouble()).toList();

  int get rank => const {'red': 0, 'amber': 1, 'grey': 2, 'green': 3}[status]!;
  String get trendArrow => trend == 'up' ? '↑' : trend == 'down' ? '↓' : '→';
}

class Ambient {
  final double o2, co, ch4;
  Ambient.fromJson(Map<String, dynamic> j)
      : o2 = (j['o2'] as num).toDouble(),
        co = (j['co'] as num).toDouble(),
        ch4 = (j['ch4'] as num).toDouble();
  bool get danger => o2 < 19.5 || co > 35 || ch4 > 1.0;
}

class AlertItem {
  final int id;
  final String severity, message;
  final String? minerId, zone, cause, action;
  final bool ack;
  AlertItem.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int,
        severity = j['severity'] as String,
        message = j['message'] as String,
        minerId = j['miner_id'] as String?,
        zone = j['zone'] as String?,
        cause = j['cause'] as String?,
        action = j['action'] as String?,
        ack = j['ack'] as bool;
}

class Kpi {
  final int total, accounted, critical, warnings;
  final double avgHr, minSpo2;
  Kpi.fromJson(Map<String, dynamic> j)
      : total = j['total'] as int,
        accounted = j['accounted'] as int,
        critical = j['critical'] as int,
        warnings = j['warnings'] as int,
        avgHr = (j['avg_hr'] as num).toDouble(),
        minSpo2 = (j['min_spo2'] as num).toDouble();
}

class Snapshot {
  final List<Miner> miners;
  final Map<String, Ambient> ambient;
  final List<AlertItem> alerts;
  final Kpi kpi;
  Snapshot.fromJson(Map<String, dynamic> j)
      : miners = (j['miners'] as List).map((e) => Miner.fromJson(e)).toList(),
        ambient = (j['ambient'] as Map<String, dynamic>)
            .map((k, v) => MapEntry(k, Ambient.fromJson(v))),
        alerts = (j['alerts'] as List).map((e) => AlertItem.fromJson(e)).toList(),
        kpi = Kpi.fromJson(j['kpi']);
}