#include <iostream>
#include <QVector>
#include <QString>
#include <cassert>
using namespace std;

int main ()
{
    QVector<QString> first;
    first << "A" << "B" << "C";
    QVector<QString> second;
    second << "A" << "B" << "C";
    assert(first == second);
  return 0;
}
