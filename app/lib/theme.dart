import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

// Swap these for your Figma palette.
Color statusColor(String s) {
  switch (s) {
    case 'red':
      return const Color(0xFFE5484D);
    case 'amber':
      return const Color(0xFFF5A524);
    case 'grey':
      return const Color(0xFF8B8D98);
    default:
      return const Color(0xFF30A46C);
  }
}

final appTheme = ThemeData(
  brightness: Brightness.dark,
  colorSchemeSeed: const Color(0xFF30A46C),
  useMaterial3: true,
);

/// Tiny line chart used for sparklines and the live waveform.
class Spark extends StatelessWidget {
  final List<double> data;
  final Color color;
  final double width;
  const Spark(this.data, {super.key, required this.color, this.width = 2});

  @override
  Widget build(BuildContext context) {
    if (data.length < 2) return const SizedBox.shrink();
    return LineChart(
      duration: Duration.zero,
      LineChartData(
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        lineTouchData: const LineTouchData(enabled: false),
        lineBarsData: [
          LineChartBarData(
            spots: [for (var i = 0; i < data.length; i++) FlSpot(i.toDouble(), data[i])],
            isCurved: true,
            color: color,
            barWidth: width,
            dotData: const FlDotData(show: false),
          )
        ],
      ),
    );
  }
}
